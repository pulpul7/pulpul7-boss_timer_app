"""Dedicated GDI capture: no PowerShell/OCR lock, no image files."""
from __future__ import annotations

import base64
import ctypes as c
from ctypes import wintypes as w
import struct
import time
import zlib

from precision_time_tracker import Pixels, Rect, Sample


def png_data(pixels: Pixels):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    width, height = pixels.rect.width, pixels.rect.height
    stride = width * 3
    rows = b''.join(b'\0' + pixels.rgb[y*stride:(y+1)*stride] for y in range(height))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows, 1)) + chunk(b'IEND', b''))
    return {'image_data': base64.b64encode(png).decode('ascii'), 'path': '', 'width': width, 'height': height}


class ScreenCapture:
    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.u = c.WinDLL('user32', use_last_error=True)
        self.g = c.WinDLL('gdi32', use_last_error=True)
        declarations = (
            (self.u, 'GetDC', [w.HWND], w.HDC),
            (self.u, 'ReleaseDC', [w.HWND, w.HDC], c.c_int),
            (self.u, 'GetClientRect', [w.HWND, c.POINTER(w.RECT)], w.BOOL),
            (self.u, 'ClientToScreen', [w.HWND, c.POINTER(w.POINT)], w.BOOL),
            (self.u, 'WindowFromPoint', [w.POINT], w.HWND),
            (self.u, 'GetAncestor', [w.HWND, w.UINT], w.HWND),
            (self.u, 'IsIconic', [w.HWND], w.BOOL),
            (self.g, 'CreateCompatibleDC', [w.HDC], w.HDC),
            (self.g, 'DeleteDC', [w.HDC], w.BOOL),
            (self.g, 'CreateDIBSection', [w.HDC, c.c_void_p, w.UINT, c.POINTER(c.c_void_p), w.HANDLE, w.DWORD], w.HBITMAP),
            (self.g, 'SelectObject', [w.HDC, w.HANDLE], w.HANDLE),
            (self.g, 'DeleteObject', [w.HANDLE], w.BOOL),
            (self.g, 'BitBlt', [w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.HDC, c.c_int, c.c_int, w.DWORD], w.BOOL),
            (self.g, 'GdiFlush', [], w.BOOL),
        )
        for dll, name, args, result in declarations:
            getattr(dll, name).argtypes = args
            getattr(dll, name).restype = result
        self.origin = self._geometry()

    def _geometry(self):
        rect, point = w.RECT(), w.POINT(0, 0)
        if (not self.u.GetClientRect(self.hwnd, c.byref(rect))
                or not self.u.ClientToScreen(self.hwnd, c.byref(point)) or self.u.IsIconic(self.hwnd)):
            raise RuntimeError('오딘 창이 닫혔거나 최소화됐습니다.')
        if (rect.right, rect.bottom) != (1600, 900):
            raise RuntimeError('초단위 찍기는 오딘 클라이언트 1600×900에서만 지원합니다.')
        return point.x, point.y

    def grab(self, rect: Rect, *, check_visible=True):
        if self._geometry() != self.origin:
            raise RuntimeError('추적 중 오딘 창 위치가 변경됐습니다.')
        x, y = self.origin
        if check_visible:
            for px, py in ((rect.left+1, rect.top+1), (rect.right-2, rect.bottom-2),
                           ((rect.left+rect.right)//2, (rect.top+rect.bottom)//2)):
                window = self.u.WindowFromPoint(w.POINT(x+px, y+py))
                ancestor = self.u.GetAncestor(window, 2)
                if (ancestor != self.u.GetAncestor(self.hwnd, 2)
                        and ancestor not in getattr(self, 'overlay_handles', ())):
                    raise RuntimeError('시간 ROI가 다른 창에 가려졌습니다. 오딘 시간표를 보이게 해주세요.')
        screen = self.u.GetDC(None)
        dc = bitmap = old = None
        try:
            dc = self.g.CreateCompatibleDC(screen)
            info = c.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, rect.width, -rect.height, 1, 32, 0, 0, 0, 0, 0, 0))
            bits = c.c_void_p()
            bitmap = self.g.CreateDIBSection(screen, info, 0, c.byref(bits), None, 0)
            if not screen or not dc or not bitmap:
                raise c.WinError(c.get_last_error())
            old = self.g.SelectObject(dc, bitmap)
            start = time.perf_counter()
            ok = self.g.BitBlt(dc, 0, 0, rect.width, rect.height, screen, x+rect.left, y+rect.top, 0x00CC0020)
            self.g.GdiFlush()
            end = time.perf_counter()
            if not ok:
                raise c.WinError(c.get_last_error())
            raw = c.string_at(bits, rect.width * rect.height * 4)
            rgb = bytearray(rect.width * rect.height * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
            return Sample(start, end, Pixels(rect, bytes(rgb)))
        finally:
            if old:
                self.g.SelectObject(dc, old)
            if bitmap:
                self.g.DeleteObject(bitmap)
            if dc:
                self.g.DeleteDC(dc)
            if screen:
                self.u.ReleaseDC(None, screen)
