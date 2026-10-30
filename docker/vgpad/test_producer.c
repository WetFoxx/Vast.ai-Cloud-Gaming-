/* test_producer — изображает Sunshine: создаёт геймпад Xbox 360 через libevdev на /dev/uinput
 * (под LD_PRELOAD=libvgpad.so это подделка) и нажимает кнопки; заодно проверяет, что клавиатуру
 * vgpad отклоняет (Sunshine тогда уходит на XTest).
 *   gcc -O2 -o test_producer test_producer.c $(pkg-config --cflags --libs libevdev)
 *   LD_PRELOAD=libvgpad.so ./test_producer [секунд]
 */
#include <errno.h>
#include <fcntl.h>
#include <libevdev/libevdev-uinput.h>
#include <libevdev/libevdev.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void emit(int fd, int type, int code, int value) {
    struct input_event ev;
    memset(&ev, 0, sizeof ev);
    ev.type = type;
    ev.code = code;
    ev.value = value;
    if (write(fd, &ev, sizeof ev) != (ssize_t)sizeof ev) perror("write");
}

int main(int argc, char **argv) {
    int secs = argc > 1 ? atoi(argv[1]) : 20;
    printf("access(/dev/uinput): %d\n", access("/dev/uinput", R_OK | W_OK));

    /* клавиатура — должна быть отклонена */
    struct libevdev *kb = libevdev_new();
    libevdev_set_name(kb, "Test keyboard");
    libevdev_enable_event_code(kb, EV_KEY, KEY_A, NULL);
    int kfd = open("/dev/uinput", O_RDWR | O_CLOEXEC | O_NONBLOCK);
    struct libevdev_uinput *ku = NULL;
    printf("клавиатура: open=%d create=%d (ждём -%d)\n", kfd >= 0, libevdev_uinput_create_from_device(kb, kfd, &ku), ENODEV);
    close(kfd);

    struct libevdev *dev = libevdev_new();
    libevdev_set_name(dev, "Test Xbox 360 pad");
    libevdev_set_id_bustype(dev, BUS_USB);
    libevdev_set_id_vendor(dev, 0x045e);
    libevdev_set_id_product(dev, 0x028e);
    libevdev_set_id_version(dev, 0x0110);
    int keys[] = {BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST, BTN_TL, BTN_TR, BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR};
    for (unsigned i = 0; i < sizeof keys / sizeof *keys; i++) libevdev_enable_event_code(dev, EV_KEY, keys[i], NULL);
    struct input_absinfo stick = {.minimum = -32768, .maximum = 32767, .fuzz = 16, .flat = 128};
    struct input_absinfo trig = {.minimum = 0, .maximum = 255};
    struct input_absinfo hat = {.minimum = -1, .maximum = 1};
    int sticks[] = {ABS_X, ABS_Y, ABS_RX, ABS_RY};
    for (int i = 0; i < 4; i++) libevdev_enable_event_code(dev, EV_ABS, sticks[i], &stick);
    libevdev_enable_event_code(dev, EV_ABS, ABS_Z, &trig);
    libevdev_enable_event_code(dev, EV_ABS, ABS_RZ, &trig);
    libevdev_enable_event_code(dev, EV_ABS, ABS_HAT0X, &hat);
    libevdev_enable_event_code(dev, EV_ABS, ABS_HAT0Y, &hat);

    int fd = open("/dev/uinput", O_RDWR | O_CLOEXEC | O_NONBLOCK);
    if (fd < 0) { perror("open /dev/uinput"); return 1; }
    struct libevdev_uinput *u = NULL;
    int r = libevdev_uinput_create_from_device(dev, fd, &u);
    printf("геймпад: create=%d devnode=%s\n", r, r == 0 ? libevdev_uinput_get_devnode(u) : "-");
    if (r) return 1;
    fflush(stdout);

    for (int t = 0; t < secs * 2; t++) {           /* каждые 0.5 с: A нажата/отпущена, стик туда-сюда */
        emit(fd, EV_KEY, BTN_SOUTH, t % 2 == 0);
        emit(fd, EV_ABS, ABS_X, (t % 4 < 2) ? 32767 : -32768);
        emit(fd, EV_SYN, SYN_REPORT, 0);
        usleep(500000);
    }
    libevdev_uinput_destroy(u);
    close(fd);
    printf("готово\n");
    return 0;
}
