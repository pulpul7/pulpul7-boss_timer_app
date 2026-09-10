"""Connection sprite selection and Tk loading; no networking or extra thread."""
from math import ceil
import tkinter as tk

FRAME_INTERVAL_MS = 160
CONNECTING_COLORS = ('#2563eb', '#7c3aed', '#b45309')


def visual_state(kind, tick=0, stopping=False):
    if stopping:
        return 1, '#b45309', '봇 종료 중…', False
    if kind == 'online':
        return 3, '#15803d', '디스코드봇 종료', False
    if kind == 'pending':
        return (0,1,2,2,1,0)[tick % 6], CONNECTING_COLORS[(tick//3) % 3], '연결 중 · 종료', True
    if kind == 'error':
        return 0, '#b91c1c', None, False
    return 0, '#5865f2', '디스코드봇 실행', False


def load_frames(master, path):
    sheet = tk.PhotoImage(master=master, file=str(path))
    width, height = sheet.width()//2, sheet.height()//2
    frames = []
    # Runtime sprite-sheet rendering only: preserve the original PNG/alpha.
    top, bottom = int(height*.24), int(height*.76)
    scale = max(1, ceil((bottom-top)/30))
    for column,row in ((0,0),(1,0),(0,1),(1,1)):
        frame = tk.PhotoImage(master=master)
        frame.tk.call(str(frame),'copy',str(sheet),'-from',column*width,row*height+top,
                      (column+1)*width,row*height+bottom,'-subsample',scale,scale)
        frames.append(frame)
    return frames
