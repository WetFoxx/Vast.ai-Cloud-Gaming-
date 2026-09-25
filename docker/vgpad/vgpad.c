/*
 * vgpad — виртуальный геймпад для Docker-контейнера без /dev/uinput и /dev/input (Vast.ai).
 *
 * Библиотека подгружается через LD_PRELOAD и играет две роли:
 *
 * 1. В Sunshine — поддельный /dev/uinput. Sunshine (libvirtualhid) проверяет access("/dev/uinput"),
 *    открывает его и создаёт устройство через libevdev_uinput_create_from_device(). Мы:
 *      - геймпад (есть BTN_SOUTH и оси) — регистрируем у посредника vgpadd, дальше write() с событиями
 *        уходят ему;
 *      - клавиатуру и мышь — отклоняем (-ENODEV): libvirtualhid сам откатывается на XTest, как без нас.
 *    Если посредник не запущен — ничего не подделываем (всё как без библиотеки).
 *
 * 2. В играх (SDL, Proton/winebus) — поддельный джойстик /dev/input/event200…207. Посредник кладёт
 *    файл-заглушку (его видят opendir/inotify), а мы на open() подключаемся к посреднику: read()/poll()
 *    работают сами (это unix-сокет), ioctl EVIOC* отвечаем по описанию геймпада, stat() показывает
 *    символьное устройство. SDL искать через udev не должна: SDL_JOYSTICK_DISABLE_UDEV=1 или файл
 *    /run/host/container-manager.
 *
 * Сборка: gcc -shared -fPIC -O2 -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=0 -o libvgpad.so vgpad.c -ldl -lpthread
 *         (и с -m32 — для 32-битных программ). Подключать: LD_PRELOAD=libvgpad.so (без пути —
 *         загрузчик сам возьмёт 32- или 64-битную из /usr/lib/<arch>).
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <poll.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/un.h>
#include <unistd.h>

#define VG_BASE 200            /* /dev/input/event200… — подальше от настоящих номеров */
#define VG_MAX 8
#define MAXFD 4096
#define DEFAULT_SOCK "/tmp/.vgpad/sock"

/* Кадры обмена с посредником: u32 длина данных, u8 тип, данные */
enum { T_CREATE = 1, T_ASSIGNED = 2, T_EVENTS = 3, T_OPEN = 4, T_DESC = 5, T_ERROR = 6 };

/* Описание геймпада — одинаково в 32 и 64 битах (посредник передаёт его как есть) */
struct vg_desc {
    uint32_t magic, version;
    struct input_id id;
    char name[80];
    uint8_t evbits[4];      /* EV_MAX 0x1f */
    uint8_t keybits[96];    /* KEY_MAX 0x2ff */
    uint8_t absbits[8];     /* ABS_MAX 0x3f */
    uint8_t mscbits[1];     /* MSC_MAX 0x07 */
    uint8_t propbits[4];    /* INPUT_PROP_MAX 0x1f */
    struct input_absinfo abs[64];
};
#define VG_MAGIC 0x44504756u  /* "VGPD" */

enum { K_NONE = 0, K_PRODUCER = 1, K_CONSUMER = 2 };
struct fdent {
    int kind;
    int pad;                 /* производитель: номер геймпада у посредника, -1 — не создан */
    int num;                 /* потребитель: номер устройства (200…) */
    struct vg_desc *desc;    /* потребитель: описание для ioctl */
};
static struct fdent fds[MAXFD];

/* Настоящие функции libc (следующие после нас в порядке загрузки) */
#define REAL(ret, name, ...) \
    static ret (*real_##name)(__VA_ARGS__); \
    if (!real_##name) real_##name = (ret (*)(__VA_ARGS__))dlsym(RTLD_NEXT, #name)

static int real_close_fd(int fd) {
    REAL(int, close, int);
    return real_close(fd);
}

static const char *sock_path(void) {
    const char *p = getenv("VGPAD_SOCK");
    return (p && *p) ? p : DEFAULT_SOCK;
}

static int broker_up(void) {
    REAL(int, access, const char *, int);
    return real_access(sock_path(), F_OK) == 0;
}

static int is_uinput_path(const char *p) {
    return p && (!strcmp(p, "/dev/uinput") || !strcmp(p, "/dev/input/uinput"));
}

/* /dev/input/event200…207 → номер, иначе -1 */
static int vg_num(const char *p) {
    static const char pre[] = "/dev/input/event";
    if (!p || strncmp(p, pre, sizeof pre - 1)) return -1;
    char *end;
    long n = strtol(p + sizeof pre - 1, &end, 10);
    if (*end || n < VG_BASE || n >= VG_BASE + VG_MAX) return -1;
    return (int)n;
}

static struct fdent *ent(int fd) {
    return (fd >= 0 && fd < MAXFD && fds[fd].kind) ? &fds[fd] : NULL;
}

/* ---------------------------------------------------------------- обмен с посредником ---- */

static int wait_fd(int fd, short ev, int ms) {
    struct pollfd p = {fd, ev, 0};
    return poll(&p, 1, ms) > 0 ? 0 : -1;
}

static int send_all(int fd, const void *buf, size_t n, int ms) {
    const char *p = buf;
    while (n) {
        ssize_t r = send(fd, p, n, MSG_NOSIGNAL);
        if (r > 0) { p += r; n -= r; continue; }
        if (r < 0 && errno == EINTR) continue;
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && wait_fd(fd, POLLOUT, ms) == 0) continue;
        return -1;
    }
    return 0;
}

static int recv_all(int fd, void *buf, size_t n, int ms) {
    char *p = buf;
    while (n) {
        ssize_t r = recv(fd, p, n, 0);
        if (r > 0) { p += r; n -= r; continue; }
        if (r < 0 && errno == EINTR) continue;
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && wait_fd(fd, POLLIN, ms) == 0) continue;
        return -1;
    }
    return 0;
}

static int send_frame(int fd, uint8_t type, const void *data, uint32_t len, int ms) {
    char hdr[5];
    memcpy(hdr, &len, 4);
    hdr[4] = (char)type;
    if (len <= 4096) {
        char buf[5 + 4096];
        memcpy(buf, hdr, 5);
        memcpy(buf + 5, data, len);
        return send_all(fd, buf, 5 + len, ms);
    }
    return send_all(fd, hdr, 5, ms) || send_all(fd, data, len, ms) ? -1 : 0;
}

/* Принять кадр нужного типа ровно len байт */
static int recv_frame(int fd, uint8_t want, void *data, uint32_t len, int ms) {
    char hdr[5];
    uint32_t n;
    if (recv_all(fd, hdr, 5, ms)) return -1;
    memcpy(&n, hdr, 4);
    if ((uint8_t)hdr[4] != want || n != len) return -1;
    return recv_all(fd, data, len, ms);
}

static int broker_connect(int cloexec) {
    int s = socket(AF_UNIX, SOCK_STREAM | (cloexec ? SOCK_CLOEXEC : 0), 0);
    if (s < 0) return -1;
    struct sockaddr_un a;
    memset(&a, 0, sizeof a);
    a.sun_family = AF_UNIX;
    strncpy(a.sun_path, sock_path(), sizeof a.sun_path - 1);
    if (connect(s, (struct sockaddr *)&a, sizeof a) < 0) {
        int e = errno;
        real_close_fd(s);
        errno = e;
        return -1;
    }
    return s;
}

/* ---------------------------------------------------------------- открытие ---------------- */

static int open_producer(int flags) {
    int s = broker_connect(flags & O_CLOEXEC);
    if (s < 0) return -1;
    if (flags & O_NONBLOCK) {
        REAL(int, fcntl, int, int, ...);
        real_fcntl(s, F_SETFL, O_NONBLOCK);
    }
    fds[s] = (struct fdent){K_PRODUCER, -1, 0, NULL};
    return s;
}

static int open_consumer(int num, int flags) {
    int s = broker_connect(flags & O_CLOEXEC);
    if (s < 0) { errno = ENOENT; return -1; }
    uint32_t req[2] = {(uint32_t)(num - VG_BASE), (uint32_t)sizeof(struct input_event)};
    struct vg_desc *d = malloc(sizeof *d);
    if (!d || send_frame(s, T_OPEN, req, sizeof req, 2000) || recv_frame(s, T_DESC, d, sizeof *d, 2000)
        || d->magic != VG_MAGIC) {
        free(d);
        real_close_fd(s);
        errno = ENOENT;
        return -1;
    }
    if (flags & O_NONBLOCK) {
        REAL(int, fcntl, int, int, ...);
        real_fcntl(s, F_SETFL, O_NONBLOCK);
    }
    fds[s] = (struct fdent){K_CONSUMER, -1, num, d};
    return s;
}

/* Общий разбор для всех вариантов open: -2 — не наш путь */
static int vg_open(const char *path, int flags) {
    int num = vg_num(path);
    if (num >= 0) return open_consumer(num, flags);
    if (is_uinput_path(path) && broker_up()) {
        int fd = open_producer(flags);
        return fd >= 0 ? fd : -2;
    }
    return -2;
}

static mode_t get_mode(int flags, va_list ap) {
    return (flags & O_CREAT) || (flags & O_TMPFILE) == O_TMPFILE ? va_arg(ap, mode_t) : 0;
}

int open(const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t m = get_mode(flags, ap); va_end(ap);
    int r = vg_open(path, flags);
    if (r != -2) return r;
    REAL(int, open, const char *, int, ...);
    return real_open(path, flags, m);
}

int open64(const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t m = get_mode(flags, ap); va_end(ap);
    int r = vg_open(path, flags);
    if (r != -2) return r;
    REAL(int, open64, const char *, int, ...);
    return real_open64(path, flags, m);
}

int openat(int dirfd, const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t m = get_mode(flags, ap); va_end(ap);
    if (path && path[0] == '/') {
        int r = vg_open(path, flags);
        if (r != -2) return r;
    }
    REAL(int, openat, int, const char *, int, ...);
    return real_openat(dirfd, path, flags, m);
}

int openat64(int dirfd, const char *path, int flags, ...) {
    va_list ap; va_start(ap, flags); mode_t m = get_mode(flags, ap); va_end(ap);
    if (path && path[0] == '/') {
        int r = vg_open(path, flags);
        if (r != -2) return r;
    }
    REAL(int, openat64, int, const char *, int, ...);
    return real_openat64(dirfd, path, flags, m);
}

int __open_2(const char *path, int flags) {
    int r = vg_open(path, flags);
    if (r != -2) return r;
    REAL(int, __open_2, const char *, int);
    return real___open_2(path, flags);
}

int __open64_2(const char *path, int flags) {
    int r = vg_open(path, flags);
    if (r != -2) return r;
    REAL(int, __open64_2, const char *, int);
    return real___open64_2(path, flags);
}

int __openat_2(int dirfd, const char *path, int flags) {
    if (path && path[0] == '/') {
        int r = vg_open(path, flags);
        if (r != -2) return r;
    }
    REAL(int, __openat_2, int, const char *, int);
    return real___openat_2(dirfd, path, flags);
}

int __openat64_2(int dirfd, const char *path, int flags) {
    if (path && path[0] == '/') {
        int r = vg_open(path, flags);
        if (r != -2) return r;
    }
    REAL(int, __openat64_2, int, const char *, int);
    return real___openat64_2(dirfd, path, flags);
}

/* Sunshine проверяет доступ к /dev/uinput до открытия */
int access(const char *path, int mode) {
    if (is_uinput_path(path) && broker_up()) return 0;
    REAL(int, access, const char *, int);
    return real_access(path, mode);
}

int faccessat(int dirfd, const char *path, int mode, int flags) {
    if (is_uinput_path(path) && broker_up()) return 0;
    REAL(int, faccessat, int, const char *, int, int);
    return real_faccessat(dirfd, path, mode, flags);
}

/* ---------------------------------------------------------------- закрытие, копии --------- */

static void forget(int fd) {
    struct fdent *e = ent(fd);
    if (!e) return;
    free(e->desc);
    *e = (struct fdent){K_NONE, -1, 0, NULL};
}

int close(int fd) {
    forget(fd);
    return real_close_fd(fd);
}

static void copy_ent(int from, int to) {
    struct fdent *e = ent(from);
    if (!e || to < 0 || to >= MAXFD || to == from) return;
    forget(to);
    fds[to] = *e;
    if (e->desc) {
        fds[to].desc = malloc(sizeof *e->desc);
        if (fds[to].desc) memcpy(fds[to].desc, e->desc, sizeof *e->desc);
    }
}

int dup(int fd) {
    REAL(int, dup, int);
    int r = real_dup(fd);
    if (r >= 0) copy_ent(fd, r);
    return r;
}

int dup2(int fd, int to) {
    REAL(int, dup2, int, int);
    if (fd != to) forget(to);
    int r = real_dup2(fd, to);
    if (r >= 0) copy_ent(fd, r);
    return r;
}

int dup3(int fd, int to, int flags) {
    REAL(int, dup3, int, int, int);
    if (fd != to) forget(to);
    int r = real_dup3(fd, to, flags);
    if (r >= 0) copy_ent(fd, r);
    return r;
}

/* ---------------------------------------------------------------- запись (Sunshine) -------- */

ssize_t write(int fd, const void *buf, size_t n) {
    struct fdent *e = ent(fd);
    if (e && e->kind == K_PRODUCER) {
        /* события геймпада → посреднику; у несозданного устройства — выбросить */
        if (e->pad >= 0 && n && n <= 4096 && send_frame(fd, T_EVENTS, buf, (uint32_t)n, 20)) {
            errno = EIO;
            return -1;
        }
        return (ssize_t)n;
    }
    /* потребитель: запись вибрации и т. п. — посредник её выбрасывает */
    REAL(ssize_t, write, int, const void *, size_t);
    return real_write(fd, buf, n);
}

/* ---------------------------------------------------------------- ioctl -------------------- */

static int copy_out(void *arg, unsigned size, const void *src, unsigned len) {
    if (!arg) { errno = EFAULT; return -1; }
    memset(arg, 0, size);
    unsigned n = len < size ? len : size;
    memcpy(arg, src, n);
    return (int)n;
}

static int copy_str(void *arg, unsigned size, const char *s) {
    if (!size) return 0;
    unsigned n = (unsigned)strlen(s) + 1;
    if (n > size) n = size;
    memcpy(arg, s, n);
    ((char *)arg)[n - 1] = 0;
    return (int)n;
}

static int evdev_ioctl(struct fdent *e, unsigned long req, void *arg) {
    const struct vg_desc *d = e->desc;
    unsigned dir = _IOC_DIR(req), nr = _IOC_NR(req), size = _IOC_SIZE(req);
    if (_IOC_TYPE(req) != 'E') { errno = ENOTTY; return -1; }

    if (req == EVIOCGVERSION) { *(int *)arg = EV_VERSION; return 0; }
    if (req == EVIOCGID) { memcpy(arg, &d->id, sizeof d->id); return 0; }
    if (req == EVIOCGRAB || req == EVIOCSCLOCKID) return 0;
#ifdef EVIOCREVOKE
    if (req == EVIOCREVOKE) return 0;
#endif
    if (req == EVIOCGEFFECTS) { *(int *)arg = 0; return 0; }

    if (dir == _IOC_READ) {
        if (nr == 0x06) return copy_str(arg, size, d->name);                 /* EVIOCGNAME */
        if (nr == 0x07) {                                                    /* EVIOCGPHYS */
            char phys[32];
            snprintf(phys, sizeof phys, "vgpad/input%d", e->num - VG_BASE);
            return copy_str(arg, size, phys);
        }
        if (nr == 0x08) { errno = ENOENT; return -1; }                       /* EVIOCGUNIQ: нет */
        if (nr == 0x09) return copy_out(arg, size, d->propbits, sizeof d->propbits);   /* EVIOCGPROP */
        if (nr >= 0x18 && nr <= 0x1b) return copy_out(arg, size, "", 0);     /* KEY/LED/SND/SW: всё отпущено */
        if (nr >= 0x20 && nr < 0x20 + EV_CNT) {                              /* EVIOCGBIT */
            unsigned ev = nr - 0x20;
            if (ev == 0) {
                uint8_t evb[4];
                memcpy(evb, d->evbits, 4);
                evb[EV_FF / 8] &= ~(1u << (EV_FF % 8));                       /* вибрацию не обещаем */
                return copy_out(arg, size, evb, sizeof evb);
            }
            if (ev == EV_KEY) return copy_out(arg, size, d->keybits, sizeof d->keybits);
            if (ev == EV_ABS) return copy_out(arg, size, d->absbits, sizeof d->absbits);
            if (ev == EV_MSC) return copy_out(arg, size, d->mscbits, sizeof d->mscbits);
            return copy_out(arg, size, "", 0);
        }
        if (nr >= 0x40 && nr < 0x40 + ABS_CNT) {                             /* EVIOCGABS */
            memcpy(arg, &d->abs[nr - 0x40], sizeof(struct input_absinfo));
            return 0;
        }
    }
    if (dir == _IOC_WRITE && nr == 0x80) { errno = EINVAL; return -1; }     /* EVIOCSFF: вибрации нет */
    if (dir == _IOC_WRITE) return 0;                                         /* EVIOCSABS, EVIOCRMFF… */
    errno = EINVAL;
    return -1;
}

int ioctl(int fd, unsigned long req, ...) {
    va_list ap;
    va_start(ap, req);
    void *arg = va_arg(ap, void *);
    va_end(ap);
    struct fdent *e = ent(fd);
    if (e && e->kind == K_CONSUMER) return evdev_ioctl(e, req, arg);
    if (e && e->kind == K_PRODUCER) {
        if (req == UI_DEV_DESTROY) return 0;
        errno = EINVAL;             /* «сырой» uinput не умеем — Sunshine откатится на XTest */
        return -1;
    }
    REAL(int, ioctl, int, unsigned long, ...);
    return real_ioctl(fd, req, arg);
}

/* ---------------------------------------------------------------- stat --------------------- */
/* SDL различает устройства по st_rdev и ждёт символьное устройство. Младший номер — заведомо несуществующий:
 * /sys в контейнере показывает устройства ХОСТА, и SDL 2.32 спрашивает udev про /sys/dev/char/13:<номер>.
 * С «честным» 13:(64+N) на хосте нашлось настоящее устройство (вход звуковой карты, не джойстик) — и SDL
 * пропускала геймпад (живой тест 2026-09-25). */
#define VG_MINOR(num) (0xFF000 + (num))
#define FIX(st, num) do { (st)->st_mode = S_IFCHR | 0666; (st)->st_rdev = makedev(13, VG_MINOR(num)); } while (0)

static int fd_num(int fd) {
    struct fdent *e = ent(fd);
    return e && e->kind == K_CONSUMER ? e->num : -1;
}

int stat(const char *p, struct stat *st) {
    REAL(int, stat, const char *, struct stat *);
    int r = real_stat(p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int lstat(const char *p, struct stat *st) {
    REAL(int, lstat, const char *, struct stat *);
    int r = real_lstat(p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int fstat(int fd, struct stat *st) {
    REAL(int, fstat, int, struct stat *);
    int r = real_fstat(fd, st), n = fd_num(fd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int fstatat(int dfd, const char *p, struct stat *st, int fl) {
    REAL(int, fstatat, int, const char *, struct stat *, int);
    int r = real_fstatat(dfd, p, st, fl), n = (p && *p) ? vg_num(p) : fd_num(dfd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int stat64(const char *p, struct stat64 *st) {
    REAL(int, stat64, const char *, struct stat64 *);
    int r = real_stat64(p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int lstat64(const char *p, struct stat64 *st) {
    REAL(int, lstat64, const char *, struct stat64 *);
    int r = real_lstat64(p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int fstat64(int fd, struct stat64 *st) {
    REAL(int, fstat64, int, struct stat64 *);
    int r = real_fstat64(fd, st), n = fd_num(fd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int fstatat64(int dfd, const char *p, struct stat64 *st, int fl) {
    REAL(int, fstatat64, int, const char *, struct stat64 *, int);
    int r = real_fstatat64(dfd, p, st, fl), n = (p && *p) ? vg_num(p) : fd_num(dfd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
/* Программы, собранные со старой glibc (< 2.33, например Steam Runtime), зовут __xstat и т. п. */
int __xstat(int v, const char *p, struct stat *st) {
    REAL(int, __xstat, int, const char *, struct stat *);
    int r = real___xstat(v, p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __lxstat(int v, const char *p, struct stat *st) {
    REAL(int, __lxstat, int, const char *, struct stat *);
    int r = real___lxstat(v, p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __fxstat(int v, int fd, struct stat *st) {
    REAL(int, __fxstat, int, int, struct stat *);
    int r = real___fxstat(v, fd, st), n = fd_num(fd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __fxstatat(int v, int dfd, const char *p, struct stat *st, int fl) {
    REAL(int, __fxstatat, int, int, const char *, struct stat *, int);
    int r = real___fxstatat(v, dfd, p, st, fl), n = (p && *p) ? vg_num(p) : fd_num(dfd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __xstat64(int v, const char *p, struct stat64 *st) {
    REAL(int, __xstat64, int, const char *, struct stat64 *);
    int r = real___xstat64(v, p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __lxstat64(int v, const char *p, struct stat64 *st) {
    REAL(int, __lxstat64, int, const char *, struct stat64 *);
    int r = real___lxstat64(v, p, st), n = vg_num(p);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __fxstat64(int v, int fd, struct stat64 *st) {
    REAL(int, __fxstat64, int, int, struct stat64 *);
    int r = real___fxstat64(v, fd, st), n = fd_num(fd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int __fxstatat64(int v, int dfd, const char *p, struct stat64 *st, int fl) {
    REAL(int, __fxstatat64, int, int, const char *, struct stat64 *, int);
    int r = real___fxstatat64(v, dfd, p, st, fl), n = (p && *p) ? vg_num(p) : fd_num(dfd);
    if (!r && n >= 0) FIX(st, n);
    return r;
}
int statx(int dfd, const char *p, int fl, unsigned mask, struct statx *st) {
    REAL(int, statx, int, const char *, int, unsigned, struct statx *);
    int r = real_statx(dfd, p, fl, mask, st), n = (p && *p) ? vg_num(p) : fd_num(dfd);
    if (!r && n >= 0) {
        st->stx_mode = S_IFCHR | 0666;
        st->stx_rdev_major = 13;
        st->stx_rdev_minor = VG_MINOR(n);
    }
    return r;
}

/* ---------------------------------------------------------------- libevdev (Sunshine) ------ */
/* Sunshine создаёт устройства через libevdev_uinput_create_from_device(). Для наших дескрипторов
 * отвечаем сами, остальное — настоящей libevdev. Геттеры libevdev берём из уже загруженной
 * Sunshine библиотеки (в играх libevdev может не быть — туда эти функции не доходят). */

struct libevdev;
struct libevdev_uinput;

struct fake_uinput {
    uint32_t magic;
    int fd, pad;
    char devnode[32];
};
#define FAKE_MAGIC 0x55475646u
static struct fake_uinput *fakes[64];

static struct fake_uinput *as_fake(const struct libevdev_uinput *u) {
    for (int i = 0; i < 64; i++)
        if (fakes[i] && (const void *)fakes[i] == (const void *)u) return fakes[i];
    return NULL;
}

#define EV(ret, name, ...) ret (*name)(__VA_ARGS__) = (ret (*)(__VA_ARGS__))dlsym(RTLD_DEFAULT, "libevdev_" #name)

static int fill_desc(const struct libevdev *dev, struct vg_desc *d) {
    EV(const char *, get_name, const struct libevdev *);
    EV(int, get_id_bustype, const struct libevdev *);
    EV(int, get_id_vendor, const struct libevdev *);
    EV(int, get_id_product, const struct libevdev *);
    EV(int, get_id_version, const struct libevdev *);
    EV(int, has_event_type, const struct libevdev *, unsigned);
    EV(int, has_event_code, const struct libevdev *, unsigned, unsigned);
    EV(int, has_property, const struct libevdev *, unsigned);
    EV(const struct input_absinfo *, get_abs_info, const struct libevdev *, unsigned);
    if (!get_name || !has_event_type || !has_event_code || !get_abs_info) return -1;

    memset(d, 0, sizeof *d);
    d->magic = VG_MAGIC;
    d->version = 1;
    d->id.bustype = get_id_bustype(dev);
    d->id.vendor = get_id_vendor(dev);
    d->id.product = get_id_product(dev);
    d->id.version = get_id_version(dev);
    snprintf(d->name, sizeof d->name, "%s", get_name(dev) ? get_name(dev) : "Virtual Gamepad");
#define SETBIT(a, b) ((a)[(b) / 8] |= (uint8_t)(1u << ((b) % 8)))
    for (unsigned t = 0; t <= EV_MAX && t < 32; t++)
        if (has_event_type(dev, t)) SETBIT(d->evbits, t);
    for (unsigned c = 0; c <= KEY_MAX; c++)
        if (has_event_code(dev, EV_KEY, c)) SETBIT(d->keybits, c);
    for (unsigned c = 0; c <= ABS_MAX; c++)
        if (has_event_code(dev, EV_ABS, c)) {
            SETBIT(d->absbits, c);
            const struct input_absinfo *ai = get_abs_info(dev, c);
            if (ai) d->abs[c] = *ai;
        }
    for (unsigned c = 0; c <= MSC_MAX; c++)
        if (has_event_code(dev, EV_MSC, c)) SETBIT(d->mscbits, c);
    if (has_property)
        for (unsigned p = 0; p <= INPUT_PROP_MAX; p++)
            if (has_property(dev, p)) SETBIT(d->propbits, p);
    return 0;
}

int libevdev_uinput_create_from_device(const struct libevdev *dev, int fd, struct libevdev_uinput **out) {
    struct fdent *e = ent(fd);
    if (!e || e->kind != K_PRODUCER) {
        int (*real)(const struct libevdev *, int, struct libevdev_uinput **) =
            (int (*)(const struct libevdev *, int, struct libevdev_uinput **))dlsym(RTLD_NEXT,
                "libevdev_uinput_create_from_device");
        return real ? real(dev, fd, out) : -ENOSYS;
    }
    EV(int, has_event_code, const struct libevdev *, unsigned, unsigned);
    EV(int, has_event_type, const struct libevdev *, unsigned);
    /* Только геймпад; клавиатуру и мышь отклоняем — Sunshine перейдёт на XTest */
    if (!has_event_code || !has_event_type || !has_event_code(dev, EV_KEY, BTN_SOUTH) || !has_event_type(dev, EV_ABS))
        return -ENODEV;
    struct vg_desc d;
    uint32_t idx;
    if (fill_desc(dev, &d) || send_frame(fd, T_CREATE, &d, sizeof d, 2000)
        || recv_frame(fd, T_ASSIGNED, &idx, sizeof idx, 2000))
        return -EIO;
    struct fake_uinput *u = calloc(1, sizeof *u);
    if (!u) return -ENOMEM;
    u->magic = FAKE_MAGIC;
    u->fd = fd;
    u->pad = (int)idx;
    snprintf(u->devnode, sizeof u->devnode, "/dev/input/event%d", VG_BASE + (int)idx);
    for (int i = 0; i < 64; i++)
        if (!fakes[i]) { fakes[i] = u; break; }
    e->pad = (int)idx;
    *out = (struct libevdev_uinput *)u;
    return 0;
}

void libevdev_uinput_destroy(struct libevdev_uinput *uinput_dev) {
    struct fake_uinput *u = as_fake(uinput_dev);
    if (!u) {
        void (*real)(struct libevdev_uinput *) =
            (void (*)(struct libevdev_uinput *))dlsym(RTLD_NEXT, "libevdev_uinput_destroy");
        if (real) real(uinput_dev);
        return;
    }
    /* Устройство исчезнет у посредника, когда Sunshine закроет дескриптор */
    struct fdent *e = ent(u->fd);
    if (e && e->kind == K_PRODUCER) e->pad = -1;
    for (int i = 0; i < 64; i++)
        if (fakes[i] == u) fakes[i] = NULL;
    free(u);
}

int libevdev_uinput_get_fd(const struct libevdev_uinput *uinput_dev) {
    struct fake_uinput *u = as_fake(uinput_dev);
    if (u) return u->fd;
    int (*real)(const struct libevdev_uinput *) =
        (int (*)(const struct libevdev_uinput *))dlsym(RTLD_NEXT, "libevdev_uinput_get_fd");
    return real ? real(uinput_dev) : -1;
}

const char *libevdev_uinput_get_devnode(struct libevdev_uinput *uinput_dev) {
    struct fake_uinput *u = as_fake(uinput_dev);
    if (u) return u->devnode;
    const char *(*real)(struct libevdev_uinput *) =
        (const char *(*)(struct libevdev_uinput *))dlsym(RTLD_NEXT, "libevdev_uinput_get_devnode");
    return real ? real(uinput_dev) : NULL;
}

const char *libevdev_uinput_get_syspath(struct libevdev_uinput *uinput_dev) {
    if (as_fake(uinput_dev)) return NULL;
    const char *(*real)(struct libevdev_uinput *) =
        (const char *(*)(struct libevdev_uinput *))dlsym(RTLD_NEXT, "libevdev_uinput_get_syspath");
    return real ? real(uinput_dev) : NULL;
}

int libevdev_uinput_write_event(const struct libevdev_uinput *uinput_dev, unsigned type, unsigned code, int value) {
    struct fake_uinput *u = as_fake(uinput_dev);
    if (u) {
        struct input_event ev;
        memset(&ev, 0, sizeof ev);
        ev.type = (uint16_t)type;
        ev.code = (uint16_t)code;
        ev.value = value;
        return write(u->fd, &ev, sizeof ev) == (ssize_t)sizeof ev ? 0 : -errno;
    }
    int (*real)(const struct libevdev_uinput *, unsigned, unsigned, int) =
        (int (*)(const struct libevdev_uinput *, unsigned, unsigned, int))dlsym(RTLD_NEXT,
            "libevdev_uinput_write_event");
    return real ? real(uinput_dev, type, code, value) : -ENOSYS;
}
