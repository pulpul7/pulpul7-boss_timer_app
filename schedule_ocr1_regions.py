"""OCR1-only pixel allowlist. Outside pixels never reach the OCR engine."""

import precision_capture_layout


def clock_rect(board, band):
    # Share the proven precision-capture clock bounds; do not keep a second
    # numeric copy that can drift away from the capture preview.
    bounds = precision_capture_layout.GAME_CLOCK_BOUNDS
    return {side: getattr(bounds, side) for side in ('left', 'top', 'right', 'bottom')}


def read_regions(app, areas, band):
    board = app._get_schedule_input_ocr2_fixed_window_rect()
    rectangles = [clock_rect(board, band)]
    # Before recognizing the chapter, retain the possible 3/4-column glyph
    # regions only. Do not use a bounding box around their union.
    for area in areas:
        for slot in app._get_schedule_ocr_slot_rects(area, 1600, 900, window_rect=board):
            for prefix in ('name', 'timer'):
                rectangles.append({side: slot[f'{prefix}_{side}'] for side in ('left', 'top', 'right', 'bottom')})
    unique = {}
    for rect in rectangles:
        key = tuple(int(rect[side]) for side in ('left', 'top', 'right', 'bottom'))
        unique[key] = dict(zip(('left', 'top', 'right', 'bottom'), key))
    return list(unique.values())


def mask_script(rectangles):
    """Mask the decoded bitmap in memory; preserve original OCR coordinates."""
    if rectangles is None:
        return '  $hasRoiMask = $false\n'
    if not rectangles:
        raise ValueError('OCR1 읽기 영역이 없습니다.')
    rows = []
    for rect in rectangles:
        left, top, right, bottom = (int(rect[key]) for key in ('left', 'top', 'right', 'bottom'))
        if not (0 <= left < right <= 1600 and 0 <= top < bottom <= 900):
            raise ValueError('OCR1 읽기 영역이 1600×900을 벗어났습니다.')
        rows.append(f'  $roiClip.Children.Add([System.Windows.Media.RectangleGeometry]::new([System.Windows.Rect]::new({left},{top},{right-left},{bottom-top})))\n')
    return (
        '  $hasRoiMask = $true\n'
        "  if ($bitmap.PixelWidth -ne 1600 -or $bitmap.PixelHeight -ne 900) { throw 'ocr1_requires_1600x900' }\n"
        '  $roiClip = [System.Windows.Media.GeometryGroup]::new()\n'
        '  $roiClip.FillRule = [System.Windows.Media.FillRule]::Nonzero\n'
        + ''.join(rows) +
        '  $roiVisual = [System.Windows.Media.DrawingVisual]::new()\n'
        '  $roiDrawing = $roiVisual.RenderOpen()\n'
        '  try {\n'
        '    $roiDrawing.DrawRectangle([System.Windows.Media.Brushes]::Black,$null,[System.Windows.Rect]::new(0,0,1600,900))\n'
        '    $roiDrawing.PushClip($roiClip)\n'
        '    $roiDrawing.DrawImage($bitmap,[System.Windows.Rect]::new(0,0,1600,900))\n'
        '    $roiDrawing.Pop()\n'
        '  } finally { $roiDrawing.Close() }\n'
        '  $roiBitmap = [System.Windows.Media.Imaging.RenderTargetBitmap]::new(1600,900,96,96,[System.Windows.Media.PixelFormats]::Pbgra32)\n'
        '  $roiBitmap.Render($roiVisual)\n'
        '  $roiBitmap.Freeze()\n'
        '  $bitmap = $roiBitmap\n')
