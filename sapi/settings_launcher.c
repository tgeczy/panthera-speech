/* settings_launcher.c -- open the settings dialog without a console flash.
 *
 * settings.cmd works, but a batch file means a console window exists for a
 * moment before PowerShell's dialog does, and that flash steals focus from
 * the window a screen reader user is about to be placed in.  This is a
 * GUI-subsystem program, so no console is ever created: PowerShell runs with
 * CREATE_NO_WINDOW and the first window that exists is the dialog itself.
 *
 * It launches settings.ps1 from its own folder, exactly as settings.cmd did,
 * and settings.cmd stays for anyone at a command line on purpose.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

int WINAPI WinMain(HINSTANCE inst, HINSTANCE prev, LPSTR cmdline, int show)
{
    WCHAR dir[MAX_PATH], line[2 * MAX_PATH];
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);

    (void)inst; (void)prev; (void)cmdline; (void)show;
    while (n && dir[n - 1] != L'\\') n--;
    dir[n] = 0;
    wsprintfW(line,
              L"powershell.exe -NoProfile -ExecutionPolicy Bypass -STA "
              L"-WindowStyle Hidden -File \"%ssettings.ps1\"", dir);
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    if (CreateProcessW(NULL, line, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                       NULL, NULL, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    return 0;
}
