/* vgrr — «монитор» для Sunshine на экране без монитора (Docker, хосты, где экраном видеокарты владеет хост).
 *
 * На части хостов Xorg в контейнере не получает права на выход видеокарты («Failed to acquire modesetting
 * permission»). Без монитора (UseDisplayDevice none) Xorg поднимается и рисует на видеокарте, X11-захват снимает
 * такой экран 60 к/с (проверено 2026-10-15, GTX 1660 S Вьетнам), но у экрана нет ни одного выхода RandR — Sunshine
 * «мониторов» не находит («Platform failed to initialize»). Sunshine загружает libXrandr.so.2 сама (dlopen) — эта
 * библиотека (имя внутри — libXrandr.so.2) подгружена в Sunshine заранее (LD_PRELOAD, только в режиме «без
 * монитора»), и dlopen отдаёт её: функции экрана — настоящей библиотеке, а когда выходов нет — один подключённый
 * выход во весь экран. Из LD_PRELOAD детей Sunshine (игры, xrandr) она себя убирает: им нужна настоящая. */
#define _GNU_SOURCE
#include <stdlib.h>
#include <string.h>

/* LD_PRELOAD без записей с «vgrr» (разделители — пробел и двоеточие) → out. 1 — что-то убрано */
int vgrr_strip(const char *in, char *out, size_t n) {
    size_t o = 0;
    int cut = 0;
    out[0] = 0;
    while (in && *in) {
        size_t len = strcspn(in, " :");
        if (len) {
            int mine = 0;
            for (size_t i = 0; i + 4 <= len; i++) if (!strncmp(in + i, "vgrr", 4)) mine = 1;
            if (mine) cut = 1;
            else if (o + len + 2 < n) { if (o) out[o++] = ' '; memcpy(out + o, in, len); o += len; out[o] = 0; }
        }
        in += len;
        while (*in == ' ' || *in == ':') in++;
    }
    return cut;
}

#ifndef VGRR_TEST
#include <X11/Xlib.h>
#include <X11/extensions/Xrandr.h>
#include <dlfcn.h>

__attribute__((constructor)) static void vgrr_hide(void) {
    char buf[4096];
    if (vgrr_strip(getenv("LD_PRELOAD"), buf, sizeof buf)) setenv("LD_PRELOAD", buf, 1);
}

#define FAKE_OUT 0x7f000001UL
#define FAKE_CRTC 0x7f000002UL

static void *real(const char *name) {
    static void *h;
    if (!h) h = dlopen("/usr/lib/x86_64-linux-gnu/libXrandr.so.2", RTLD_NOW | RTLD_LOCAL);
    return h ? dlsym(h, name) : NULL;
}

static RROutput outs[1] = {FAKE_OUT};
static RRCrtc crtcs[1] = {FAKE_CRTC};
static XRRScreenResources fake_res;
static char out_name[] = "VIRTUAL-0";
static XRROutputInfo fake_out;
static XRRCrtcInfo fake_crtc;

XRRScreenResources *XRRGetScreenResources(Display *d, Window w) {
    XRRScreenResources *(*f)(Display *, Window) = real("XRRGetScreenResources");
    XRRScreenResources *r = f ? f(d, w) : NULL;
    if (r && r->noutput > 0) return r;
    void (*fr)(XRRScreenResources *) = real("XRRFreeScreenResources");
    if (r && fr) fr(r);
    memset(&fake_res, 0, sizeof fake_res);
    fake_res.noutput = 1; fake_res.outputs = outs;
    fake_res.ncrtc = 1; fake_res.crtcs = crtcs;
    return &fake_res;
}

XRRScreenResources *XRRGetScreenResourcesCurrent(Display *d, Window w) { return XRRGetScreenResources(d, w); }

XRROutputInfo *XRRGetOutputInfo(Display *d, XRRScreenResources *res, RROutput o) {
    if (o != FAKE_OUT) {
        XRROutputInfo *(*f)(Display *, XRRScreenResources *, RROutput) = real("XRRGetOutputInfo");
        return f ? f(d, res, o) : NULL;
    }
    memset(&fake_out, 0, sizeof fake_out);
    fake_out.crtc = FAKE_CRTC; fake_out.name = out_name; fake_out.nameLen = sizeof out_name - 1;
    fake_out.connection = RR_Connected; fake_out.ncrtc = 1; fake_out.crtcs = crtcs;
    return &fake_out;
}

XRRCrtcInfo *XRRGetCrtcInfo(Display *d, XRRScreenResources *res, RRCrtc c) {
    if (c != FAKE_CRTC) {
        XRRCrtcInfo *(*f)(Display *, XRRScreenResources *, RRCrtc) = real("XRRGetCrtcInfo");
        return f ? f(d, res, c) : NULL;
    }
    memset(&fake_crtc, 0, sizeof fake_crtc);
    fake_crtc.width = (unsigned) DisplayWidth(d, DefaultScreen(d));
    fake_crtc.height = (unsigned) DisplayHeight(d, DefaultScreen(d));
    fake_crtc.noutput = 1; fake_crtc.outputs = outs;
    return &fake_crtc;
}

void XRRFreeScreenResources(XRRScreenResources *r) {
    if (r == &fake_res) return;
    void (*f)(XRRScreenResources *) = real("XRRFreeScreenResources");
    if (f) f(r);
}
void XRRFreeOutputInfo(XRROutputInfo *o) {
    if (o == &fake_out) return;
    void (*f)(XRROutputInfo *) = real("XRRFreeOutputInfo");
    if (f) f(o);
}
void XRRFreeCrtcInfo(XRRCrtcInfo *c) {
    if (c == &fake_crtc) return;
    void (*f)(XRRCrtcInfo *) = real("XRRFreeCrtcInfo");
    if (f) f(c);
}
#endif
