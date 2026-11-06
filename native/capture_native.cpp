// Capture Facile v5 - Win32 GDI capture engine (not a DRM bypass).
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <cstring>

extern "C" __declspec(dllexport) int __cdecl CaptureDesktopBGRA(
    void* destination, int left, int top, int width, int height) {
    if (!destination || width < 1 || height < 1 || width > 32768 || height > 32768)
        return 1;
    HDC screen = GetDC(nullptr);
    if (!screen) return 2;
    HDC memory = CreateCompatibleDC(screen);
    if (!memory) { ReleaseDC(nullptr, screen); return 3; }
    BITMAPINFO info = {};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height; // top-down BGRA
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    void* pixels = nullptr;
    HBITMAP bitmap = CreateDIBSection(screen, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
    if (!bitmap || !pixels) {
        if (bitmap) DeleteObject(bitmap);
        DeleteDC(memory);
        ReleaseDC(nullptr, screen);
        return 4;
    }
    HGDIOBJ old = SelectObject(memory, bitmap);
    BOOL ok = BitBlt(memory, 0, 0, width, height, screen, left, top, SRCCOPY | CAPTUREBLT);
    if (ok) std::memcpy(destination, pixels, static_cast<size_t>(width) * height * 4);
    SelectObject(memory, old);
    DeleteObject(bitmap);
    DeleteDC(memory);
    ReleaseDC(nullptr, screen);
    return ok ? 0 : 5;
}
