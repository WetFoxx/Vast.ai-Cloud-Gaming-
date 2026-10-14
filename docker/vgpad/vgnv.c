/* vgnv — кодировщик NVIDIA (NVENC) на многокарточных хостах Vast в Docker.
 *
 * Хост сдаёт одну видеокарту из нескольких; в контейнере видна только она. Библиотека кодировщика (драйвер 570+)
 * спрашивает драйвер «какие видеокарты подключены» (NV0000_CTRL_CMD_GPU_GET_ATTACHED_IDS) — драйвер отвечает списком
 * ВСЕХ карт хоста, библиотека пытается открыть каждую, натыкается на чужие и сдаётся: «OpenEncodeSessionEx failed:
 * unsupported device (2)» (NVIDIA/nvidia-container-toolkit#1249; у нас — 151 из 226 машин Docker, 2026-10-15).
 *
 * Обход — тот же, что ядерный kretprobe из того обсуждения, но внутри контейнера и только для Sunshine (LD_PRELOAD):
 * после ответа драйвера вычёркиваем из списка карты, чей адрес PCI (NV0000_CTRL_CMD_GPU_GET_PCI_INFO) не из
 * VG_NV_KEEP — адресов своих карт из nvidia-smi («00000000:04:00.0,…»). Не смогли узнать адрес — карту не трогаем;
 * своих в списке не нашлось — список не трогаем. Хост, драйвер и другие программы это не затрагивает.
 * Форматы — из открытых исходников драйвера (open-gpu-kernel-modules: nvos.h, ctrl0000gpu.h, nv_escape.h). */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define NV_ESC_RM_CONTROL 0x2A
#define CMD_ATTACHED 0x201U          /* NV0000_CTRL_CMD_GPU_GET_ATTACHED_IDS */
#define CMD_PROBED 0x214U            /* NV0000_CTRL_CMD_GPU_GET_PROBED_IDS */
#define CMD_PCI 0x21bU               /* NV0000_CTRL_CMD_GPU_GET_PCI_INFO */
#define MAX_GPUS 32U
#define INVALID_ID 0xffffffffU

typedef struct {                     /* NVOS54_PARAMETERS */
    uint32_t hClient, hObject, cmd, flags;
    uint64_t params __attribute__((aligned(8)));
    uint32_t paramsSize, status;
} vg_nvos54;

typedef struct {                     /* NV0000_CTRL_GPU_GET_PCI_INFO_PARAMS */
    uint32_t gpuId, domain;
    uint16_t bus, slot;
} vg_pci;

_Static_assert(sizeof(vg_nvos54) == 32, "NVOS54_PARAMETERS");
_Static_assert(sizeof(vg_pci) == 12, "NV0000_CTRL_GPU_GET_PCI_INFO_PARAMS");

static struct { uint32_t dom; uint16_t bus, slot; } keep[8];
static int nkeep = -1;

/* «00000000:04:00.0,0000:A2:00.0» → keep */
int vg_parse_keep(const char *s) {
    nkeep = 0;
    while (s && *s && nkeep < 8) {
        unsigned dom = 0, bus = 0, slot = 0, fn = 0;
        if (sscanf(s, "%x:%x:%x.%x", &dom, &bus, &slot, &fn) == 4) {
            keep[nkeep].dom = dom; keep[nkeep].bus = (uint16_t)bus; keep[nkeep].slot = (uint16_t)slot;
            nkeep++;
        }
        s = strchr(s, ',');
        if (s) s++;
    }
    return nkeep;
}

/* pci(id, &адрес) → 0 — адрес узнан. Вычеркнуть чужие из ids[0..max) (конец списка — INVALID_ID), сдвинув
 * остальные (и flags, если есть, — у PROBED флаги идут параллельно). Возвращает, сколько осталось. */
int vg_filter(uint32_t *ids, uint32_t *flags, unsigned max, int (*pci)(uint32_t, vg_pci *, void *), void *ctx) {
    uint32_t out[MAX_GPUS], outf[MAX_GPUS];
    unsigned n = 0, total = 0, mine = 0;
    if (nkeep <= 0 || max > MAX_GPUS) return -1;
    for (unsigned i = 0; i < max && ids[i] != INVALID_ID; i++) {
        vg_pci p = {ids[i], 0, 0, 0};
        int known = pci(ids[i], &p, ctx) == 0, ok = !known;
        total++;
        for (int k = 0; known && k < nkeep; k++)
            if (p.domain == keep[k].dom && p.bus == keep[k].bus && p.slot == keep[k].slot) { ok = 1; mine++; }
        if (ok) { out[n] = ids[i]; outf[n] = flags ? flags[i] : 0; n++; }
    }
    if (!mine) return (int)total;                    /* своей карты не нашли — ничего не трогаем */
    for (unsigned i = 0; i < max; i++) {
        ids[i] = i < n ? out[i] : INVALID_ID;
        if (flags) flags[i] = i < n ? outf[i] : 0;
    }
    return (int)n;
}

#ifndef VGNV_TEST
#include <dlfcn.h>
#include <stdarg.h>
#include <sys/ioctl.h>

static int (*real_ioctl)(int, unsigned long, ...);

struct q { int fd; unsigned long req; uint32_t hClient, hObject; };

static int ask_pci(uint32_t id, vg_pci *p, void *ctx) {
    struct q *c = ctx;
    vg_nvos54 a = {c->hClient, c->hObject, CMD_PCI, 0, (uint64_t)(uintptr_t)p, sizeof *p, 0};
    p->gpuId = id;
    return real_ioctl(c->fd, c->req, &a) == 0 && a.status == 0 ? 0 : -1;
}

int ioctl(int fd, unsigned long req, ...) {
    va_list ap;
    va_start(ap, req);
    void *arg = va_arg(ap, void *);
    va_end(ap);
    if (!real_ioctl) real_ioctl = (int (*)(int, unsigned long, ...))dlsym(RTLD_NEXT, "ioctl");
    int r = real_ioctl(fd, req, arg);
    if (r != 0 || _IOC_TYPE(req) != 'F' || _IOC_NR(req) != NV_ESC_RM_CONTROL || _IOC_SIZE(req) != sizeof(vg_nvos54))
        return r;
    if (nkeep < 0) vg_parse_keep(getenv("VG_NV_KEEP"));
    vg_nvos54 *p = arg;
    if (!nkeep || !p || p->status != 0 || !p->params) return r;
    struct q c = {fd, req, p->hClient, p->hObject};
    uint32_t *ids = (uint32_t *)(uintptr_t)p->params;
    unsigned before = 0;
    int left = -2;
    if (p->cmd != CMD_ATTACHED && p->cmd != CMD_PROBED) return r;
    while (before < MAX_GPUS && ids[before] != INVALID_ID) before++;
    if (p->cmd == CMD_ATTACHED && p->paramsSize >= MAX_GPUS * 4)
        left = vg_filter(ids, NULL, MAX_GPUS, ask_pci, &c);
    else if (p->cmd == CMD_PROBED && p->paramsSize >= MAX_GPUS * 4 * 3)
        left = vg_filter(ids, ids + 2 * MAX_GPUS, MAX_GPUS, ask_pci, &c);   /* gpuIds, excludedGpuIds, gpuFlags */
    const char *lp = getenv("VG_NV_LOG");                                 /* для диагностики видео */
    FILE *lf = lp ? fopen(lp, "a") : NULL;
    if (lf) {
        fprintf(lf, "vgnv: %s — карт у драйвера %u, оставлено %d\n", p->cmd == CMD_ATTACHED ? "attached" : "probed",
                before, left);
        fclose(lf);
    }
    return r;
}
#endif
