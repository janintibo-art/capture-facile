// Capture Facile v6 - original, minimal Direct3D 11 Desktop Duplication capture.
// Windows DXGI returns only pixels that the operating system permits to capture.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <wrl/client.h>
#include <cstdint>
#include <cstring>
#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
using Microsoft::WRL::ComPtr;

// Return codes: 0 success, 1 args, 2 DXGI factory, 3 output absent,
// 4 D3D device, 5 duplication unavailable, 6 timeout, 7 frame error,
// 8 small destination buffer, 9 staging failure, 10 map failure,
// 11 unsupported screen rotation/format.
extern "C" __declspec(dllexport) int __cdecl CaptureDXGIPrimaryBGRA(
    void* destination, uint64_t capacity, int* result_width, int* result_height) {
    if (!result_width || !result_height) return 1;
    *result_width = 0;
    *result_height = 0;
    ComPtr<IDXGIFactory1> factory;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1),
          reinterpret_cast<void**>(factory.GetAddressOf())))) return 2;

    ComPtr<IDXGIAdapter1> chosen_adapter;
    ComPtr<IDXGIOutput> chosen_output;
    for (UINT ai = 0; ; ++ai) {
        ComPtr<IDXGIAdapter1> adapter;
        if (factory->EnumAdapters1(ai, adapter.GetAddressOf()) == DXGI_ERROR_NOT_FOUND) break;
        for (UINT oi = 0; ; ++oi) {
            ComPtr<IDXGIOutput> output;
            if (adapter->EnumOutputs(oi, output.GetAddressOf()) == DXGI_ERROR_NOT_FOUND) break;
            DXGI_OUTPUT_DESC desc = {};
            if (FAILED(output->GetDesc(&desc)) || !desc.AttachedToDesktop) continue;
            const RECT& r = desc.DesktopCoordinates;
            // Primary monitor uses origin (0,0) in virtual desktop coordinates.
            if (r.left <= 0 && r.right > 0 && r.top <= 0 && r.bottom > 0) {
                chosen_adapter = adapter;
                chosen_output = output;
                break;
            }
        }
        if (chosen_output) break;
    }
    if (!chosen_output) return 3;
    DXGI_OUTPUT_DESC out_desc = {};
    if (FAILED(chosen_output->GetDesc(&out_desc))) return 3;
    if (out_desc.Rotation != DXGI_MODE_ROTATION_IDENTITY &&
        out_desc.Rotation != DXGI_MODE_ROTATION_UNSPECIFIED) return 11;

    ComPtr<ID3D11Device> device;
    ComPtr<ID3D11DeviceContext> context;
    const D3D_FEATURE_LEVEL levels[] = { D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0,
        D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0 };
    D3D_FEATURE_LEVEL obtained = D3D_FEATURE_LEVEL_10_0;
    HRESULT hr = D3D11CreateDevice(chosen_adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, levels, 4, D3D11_SDK_VERSION,
        device.GetAddressOf(), &obtained, context.GetAddressOf());
    if (FAILED(hr)) {
        hr = D3D11CreateDevice(chosen_adapter.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, levels + 1, 3, D3D11_SDK_VERSION,
            device.GetAddressOf(), &obtained, context.GetAddressOf());
    }
    if (FAILED(hr)) return 4;
    ComPtr<IDXGIOutput1> output1;
    if (FAILED(chosen_output.As(&output1))) return 5;
    ComPtr<IDXGIOutputDuplication> duplication;
    if (FAILED(output1->DuplicateOutput(device.Get(), duplication.GetAddressOf()))) return 5;

    DXGI_OUTDUPL_FRAME_INFO frame_info = {};
    ComPtr<IDXGIResource> resource;
    hr = duplication->AcquireNextFrame(850, &frame_info, resource.GetAddressOf());
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) return 6;
    if (FAILED(hr)) return 7;

    // Scoped guard always releases the acquired frame, including all early returns.
    struct FrameGuard {
        IDXGIOutputDuplication* value;
        ~FrameGuard() { value->ReleaseFrame(); }
    } guard{duplication.Get()};

    ComPtr<ID3D11Texture2D> surface;
    if (FAILED(resource.As(&surface))) return 7;
    D3D11_TEXTURE2D_DESC desc = {};
    surface->GetDesc(&desc);
    if (desc.Format != DXGI_FORMAT_B8G8R8A8_UNORM || !desc.Width || !desc.Height ||
        desc.Width > 16384 || desc.Height > 16384) return 11;
    *result_width = static_cast<int>(desc.Width);
    *result_height = static_cast<int>(desc.Height);
    const uint64_t needed = uint64_t(desc.Width) * uint64_t(desc.Height) * 4;
    if (!destination || capacity < needed) return 8;

    D3D11_TEXTURE2D_DESC copy_desc = desc;
    copy_desc.Usage = D3D11_USAGE_STAGING;
    copy_desc.BindFlags = 0;
    copy_desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    copy_desc.MiscFlags = 0;
    copy_desc.MipLevels = 1;
    copy_desc.ArraySize = 1;
    copy_desc.SampleDesc.Count = 1;
    copy_desc.SampleDesc.Quality = 0;
    ComPtr<ID3D11Texture2D> staging;
    if (FAILED(device->CreateTexture2D(&copy_desc, nullptr, staging.GetAddressOf()))) return 9;
    context->CopyResource(staging.Get(), surface.Get());
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (FAILED(context->Map(staging.Get(), 0, D3D11_MAP_READ, 0, &mapped))) return 10;
    unsigned char* out = static_cast<unsigned char*>(destination);
    const unsigned char* in = static_cast<const unsigned char*>(mapped.pData);
    const size_t bytes_per_row = static_cast<size_t>(desc.Width) * 4;
    for (UINT y = 0; y < desc.Height; ++y)
        std::memcpy(out + size_t(y) * bytes_per_row, in + size_t(y) * mapped.RowPitch, bytes_per_row);
    context->Unmap(staging.Get(), 0);
    return 0;
}
