/* sdltest — видит ли SDL виртуальный геймпад и нажатия (вместо sdl2-jstest, которого нет в Ubuntu 22.04).
 *   gcc -O2 -o sdltest sdltest.c $(pkg-config --cflags --libs sdl2)
 *   LD_PRELOAD=libvgpad.so SDL_JOYSTICK_DISABLE_UDEV=1 ./sdltest [секунд]
 */
#include <SDL2/SDL.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    int secs = argc > 1 ? atoi(argv[1]) : 5;
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (SDL_Init(SDL_INIT_JOYSTICK | SDL_INIT_GAMECONTROLLER) != 0) {
        printf("SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    SDL_version v;
    SDL_GetVersion(&v);
    printf("SDL %d.%d.%d, джойстиков: %d\n", v.major, v.minor, v.patch, SDL_NumJoysticks());
    for (int i = 0; i < SDL_NumJoysticks(); i++) {
        int gc = SDL_IsGameController(i);
        printf("  %d: «%s», геймпад SDL: %s\n", i, SDL_JoystickNameForIndex(i), gc ? "да" : "нет");
        if (gc) SDL_GameControllerOpen(i); else SDL_JoystickOpen(i);
    }
    fflush(stdout);
    int buttons = 0, axes = 0;
    Uint32 end = SDL_GetTicks() + (Uint32)secs * 1000;
    while (SDL_GetTicks() < end) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_CONTROLLERBUTTONDOWN) {
                buttons++;
                printf("кнопка: %s\n", SDL_GameControllerGetStringForButton(e.cbutton.button));
            } else if (e.type == SDL_JOYBUTTONDOWN) {
                buttons++;
                printf("кнопка джойстика: %d\n", e.jbutton.button);
            } else if (e.type == SDL_CONTROLLERAXISMOTION || e.type == SDL_JOYAXISMOTION) {
                axes++;
            } else if (e.type == SDL_CONTROLLERDEVICEADDED || e.type == SDL_JOYDEVICEADDED) {
                printf("подключено устройство\n");
            }
            fflush(stdout);
        }
        SDL_Delay(5);
    }
    printf("итог: нажатий %d, движений осей %d\n", buttons, axes);
    SDL_Quit();
    return 0;
}
