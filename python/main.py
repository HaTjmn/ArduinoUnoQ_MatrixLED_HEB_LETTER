# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge, FrameDesigner, Logger
from app_frame import AppFrame  # user module defining AppFrame
import store  # user module for DB operations
import numpy as np
import os
import re
import sys
import threading
from pathlib import Path

BRIGHTNESS_LEVELS = 8  # must match the frontend slider range (0..BRIGHTNESS_LEVELS-1)
MAX_FRAMES = 300  # must match MAX_FRAMES in sketch.ino (animation buffer limit)
MATRIX_ROWS = 8
MATRIX_COLS = 13
SKETCH_ANIMATION_H = Path(__file__).resolve().parent.parent / "sketch" / "Animation.h"

# Hebrew letters in Unicode block order (U+05D0 Alef .. U+05EA Tav), the same
# order as hebrewLetterToFrame[] in sketch.ino. Frames are matched to a
# letter by looking for one of these characters in the frame's name/comment.
HEBREW_LETTERS_ORDER = "אבגדהוזחטיךכלםמןנסעףפץצקרשת"
HEBREW_LETTER_LABELS = [
    "alef", "bet", "gimel", "dalet", "he", "vav", "zayin", "het", "tet", "yod",
    "final-kaf", "kaf", "lamed", "final-mem", "mem", "final-nun", "nun", "samekh",
    "ayin", "final-pe", "pe", "final-tsadi", "tsadi", "qof", "resh", "shin", "tav",
]

logger = Logger("led-matrix-painter")

# Log every Python function entry in the project at INFO level.  Keeping this
# instrumentation in one place avoids adding repetitive logging statements to
# every function and also covers class methods and functions executed by the
# WebUI worker threads.
_LOGGED_PYTHON_FILES = {
    "main.py",
    "app.py",
    "app_frame.py",
    "store.py",
}


def _function_info_profiler(frame, event, arg):
    """Emit one INFO message whenever a project Python function is entered."""
    if event != "call":
        return

    code = frame.f_code
    filename = os.path.basename(code.co_filename)
    if filename not in _LOGGED_PYTHON_FILES:
        return

    logger.info(
        f"FUNCTION_ENTRY file={filename} "
        f"line={code.co_firstlineno} function={code.co_name}"
    )


def _enable_function_info_logging():
    """Enable function-entry logging for this thread and future threads."""
    sys.setprofile(_function_info_profiler)
    threading.setprofile(_function_info_profiler)


_enable_function_info_logging()

ui = WebUI()
designer = FrameDesigner()

logger.info("Initializing LED matrix tool")
store.init_db()
logger.info(f"Database initialized, brightness_levels={BRIGHTNESS_LEVELS}")


def get_config():
    """Expose runtime configuration for the frontend."""
    return {
        'brightness_levels': BRIGHTNESS_LEVELS,
        'width': designer.width,
        'height': designer.height,
    }


def apply_frame_to_board(frame: AppFrame):
    """Send frame bytes to the Arduino board."""
    frame_bytes = frame.to_board_bytes()
    Bridge.call("draw", frame_bytes)
    frame_label = f"name={frame.name}, id={frame.id if frame.id else 'None (preview)'}"
    logger.debug(f"Frame sent to board: {frame_label}, bytes_len={len(frame_bytes)}")


def update_board(payload: dict):
    """Update board display in real-time without persisting to DB.

    Used for live preview during editing.
    Expected payload: {rows, name, id, position, duration_ms, brightness_levels}
    """
    frame = AppFrame.from_json(payload)
    apply_frame_to_board(frame)
    vector_text = frame.to_c_string()
    return {'ok': True, 'vector': vector_text}


def persist_frame(payload: dict):
    """Persist frame to DB (insert new or update existing).

    Backend (store.save_frame) is responsible for assigning progressive names.

    Expected payload: {rows, name, id, position, duration_ms, brightness_levels}
    """
    frame = AppFrame.from_json(payload)

    if frame.id is None:
        # Insert new frame - backend assigns name if empty
        logger.debug(f"Creating new frame: name='{frame.name}'")
        frame.id = store.save_frame(frame)
        # Reload frame to get backend-assigned name
        record = store.get_frame_by_id(frame.id)
        if record:
            frame = AppFrame.from_record(record)
        logger.info(f"New frame created: id={frame.id}, name={frame.name}")
    else:
        # Update existing frame
        logger.debug(f"Updating frame: id={frame.id}, name={frame.name}")
        store.update_frame(frame)

    apply_frame_to_board(frame)
    vector_text = frame.to_c_string()
    return {'ok': True, 'frame': frame.to_json(), 'vector': vector_text}


def bulk_update_frame_duration(payload) -> bool:
    """Update the duration of all frames."""
    duration = payload.get('duration_ms', 1000)
    logger.debug(f"Bulk updating frame duration: duration={duration}")
    store.bulk_update_frame_duration(duration)
    return True


def load_frame(payload: dict = None):
    """Load a frame for editing or create empty if none exist.

    Optional payload: {id: int} to load specific frame
    If no ID provided, loads last frame or creates empty
    """
    fid = payload.get('id') if payload else None

    if fid is not None:
        logger.debug(f"Loading frame by id: {fid}")
        record = store.get_frame_by_id(fid)
        if not record:
            logger.warning(f"Frame not found: id={fid}")
            return {'error': 'frame not found'}
        frame = AppFrame.from_record(record)
        logger.info(f"Frame loaded: id={frame.id}, name={frame.name}")
    else:
        # Get last frame or create empty
        logger.debug("Loading last frame or creating empty")
        frame = store.get_or_create_active_frame(brightness_levels=BRIGHTNESS_LEVELS)
        logger.info(f"Active frame ready: id={frame.id}, name={frame.name}")

    apply_frame_to_board(frame)
    vector_text = frame.to_c_string()
    return {'ok': True, 'frame': frame.to_json(), 'vector': vector_text}


def list_frames():
    """Return list of frames for sidebar."""
    records = store.list_frames(order_by='position ASC, id ASC')
    frames = [AppFrame.from_record(r).to_json() for r in records]
    return {'frames': frames}


def get_frame(payload: dict):
    """Get single frame by ID."""
    fid = payload.get('id')
    record = store.get_frame_by_id(fid)

    if not record:
        return {'error': 'not found'}

    frame = AppFrame.from_record(record)
    return {'frame': frame.to_json()}


def delete_frames(payload: dict):
    """Delete multiple frames by ID."""
    fids = payload.get('ids', [])
    if not fids:
        return {'error': 'no frame ids provided'}
    logger.info(f"Deleting frames: ids={fids}")
    store.delete_frames(fids)
    return {'ok': True}


def reorder_frames(payload: dict):
    """Reorder frames to match provided id list order."""
    order = payload.get('order', [])
    logger.info(f"Reordering frames: new order={order}")
    store.reorder_frames(order)
    return {'ok': True}


def transform_frame(payload: dict):
    """Apply transformation operation to a frame.

    Payload: {op: str, rows: list OR id: int}
    Operations: invert, invert_not_null, rotate180, flip_h, flip_v
    """
    op = payload.get('op')
    if not op:
        return {'error': 'op required'}

    # Load frame from rows or by ID
    rows = payload.get('rows')
    if rows is not None:
        frame = AppFrame.from_json({'rows': rows, 'brightness_levels': BRIGHTNESS_LEVELS})
        logger.debug(f"Transforming frame from rows: op={op}")
    else:
        fid = payload.get('id')
        if fid is None:
            return {'error': 'id or rows required'}
        record = store.get_frame_by_id(fid)
        if not record:
            return {'error': 'frame not found'}
        frame = AppFrame.from_record(record)
        logger.debug(f"Transforming frame by id: id={fid}, op={op}")

    # Apply transformation
    operations = {
        'invert': designer.invert,
        'invert_not_null': designer.invert_not_null,
        'rotate180': designer.rotate180,
        'flip_h': designer.flip_horizontally,
        'flip_v': designer.flip_vertically,
    }
    if op not in operations:
        logger.warning(f"Unsupported transform operation: {op}")
        return {'error': 'unsupported op'}

    options = payload.get('options', {})
    operations[op](frame, **options)
    logger.info(f"Transform applied: op={op}")

    # Return transformed frame (frontend will handle board update via persist)
    return {'ok': True, 'frame': frame.to_json(), 'vector': frame.to_c_string()}


def export_frames(payload: dict = None):
    """Export multiple frames into a single C header string.

    Payload (optional): {frames: [id,...], animations: [{name, frames}]}
    - If no animations: exports frames as individual arrays (Frames mode)
    - If animations present: exports as animation sequences (Animations mode)
    """
    # Get frame IDs to export
    if payload and payload.get('frames'):
        frame_ids = [int(fid) for fid in payload['frames']]
        logger.info(f"Exporting selected frames: ids={frame_ids}")
        records = [store.get_frame_by_id(fid) for fid in frame_ids]
        records = [r for r in records if r is not None]
    else:
        logger.info("Exporting all frames")
        records = store.list_frames(order_by='position ASC, id ASC')

    logger.debug(f"Exporting {len(records)} frames to C header")

    # Build frame objects and check for duplicate names
    frames = [AppFrame.from_record(r) for r in records]
    frame_names = {}  # name -> count
    for frame in frames:
        frame_names[frame.name] = frame_names.get(frame.name, 0) + 1

    # Assign unique names if duplicates exist
    name_counters = {}  # name -> current index
    for frame in frames:
        if frame_names[frame.name] > 1:
            # Duplicate detected, add suffix
            if frame.name not in name_counters:
                name_counters[frame.name] = 0
            # Use _idN suffix for uniqueness
            frame._export_name = f"{frame.name}_id{frame.id}"
            logger.debug(f"Duplicate name '{frame.name}' -> '{frame._export_name}'")
        else:
            # Unique name, use as-is
            frame._export_name = frame.name

    # Check if we're in animations mode
    animations = payload.get('animations') if payload else None

    if animations:
        # Animation mode: export as animation sequences
        logger.info(f"Animation mode: {len(animations)} animation(s)")
        header_parts = []

        for anim in animations:
            anim_name = anim.get('name', 'Animation')
            anim_frame_ids = anim.get('frames', [])

            # Get frames for this animation
            anim_frames = [f for f in frames if f.id in anim_frame_ids]

            if not anim_frames:
                continue

            # Build animation array (delegated to AppFrame exporter)
            header_parts.append(f"// Animation: {anim_name}")
            header_parts.append(AppFrame.frames_to_c_animation_array(anim_frames, anim_name))

        header = "\n".join(header_parts).strip() + "\n"
        return {'header': header}
    else:
        # Frames mode: export individual frame arrays
        header_parts = []
        for frame in frames:
            header_parts.append(f"// {frame._export_name} (id {frame.id})")
            header_parts.append(frame.to_c_string())

        header = "\n".join(header_parts).strip() + "\n"
        return {'header': header}


_ANIMATION_ENTRY_RE = re.compile(
    r"\{\s*(0[xX][0-9a-fA-F]+)\s*,\s*(0[xX][0-9a-fA-F]+)\s*,\s*(0[xX][0-9a-fA-F]+)\s*,\s*(0[xX][0-9a-fA-F]+)\s*,\s*(\d+)\s*\}\s*,?\s*(?:/{2}\s*(.*))?"
)
_FRAME_ARRAY_RE = re.compile(r"uint8_t\s+(\w+)\s*\[\]\s*=\s*\{([^}]*)\}\s*;", re.DOTALL)


def _hex_words_to_rows(hex_words: list[int], on_value: int) -> list[list[int]]:
    """Unpack 4 uint32_t bit-packed words (row-major, MSB first) into an 8x13 grid.

    Mirrors AppFrame.to_animation_hex(), which only preserves on/off state
    (no grayscale), so imported animation frames come back as binary.
    """
    bits = []
    for word in hex_words:
        for j in range(32):
            bits.append((word >> (31 - j)) & 1)
    rows = []
    idx = 0
    for _ in range(MATRIX_ROWS):
        row = []
        for _ in range(MATRIX_COLS):
            row.append(on_value if bits[idx] else 0)
            idx += 1
        rows.append(row)
    return rows


def _parse_animation_header(content: str, on_value: int) -> list[dict]:
    """Parse `const uint32_t NAME[][5] = {...}` animation sequences (Animation mode export)."""
    parsed = []
    for m in _ANIMATION_ENTRY_RE.finditer(content):
        hex_words = [int(m.group(i), 16) for i in range(1, 5)]
        duration_ms = int(m.group(5))
        name = (m.group(6) or "").strip()
        parsed.append({
            "name": name,
            "rows": _hex_words_to_rows(hex_words, on_value),
            "duration_ms": duration_ms,
        })
    return parsed


def _parse_frames_header(content: str) -> list[dict]:
    """Parse `uint8_t NAME [] = {...}` individual frame arrays (Frames mode export)."""
    parsed = []
    for m in _FRAME_ARRAY_RE.finditer(content):
        name = m.group(1)
        try:
            values = [int(v.strip()) for v in m.group(2).split(',') if v.strip() != '']
        except ValueError:
            continue
        if len(values) != MATRIX_ROWS * MATRIX_COLS:
            logger.warning(f"import_frames: skipping array '{name}', unexpected size {len(values)}")
            continue
        rows = [values[r * MATRIX_COLS:(r + 1) * MATRIX_COLS] for r in range(MATRIX_ROWS)]
        parsed.append({"name": name, "rows": rows, "duration_ms": 1000})
    return parsed


def import_frames(payload: dict):
    """Import frames from an uploaded .h file's text content, so editing can resume.

    Payload: {content: str} - raw text of a .h file previously produced by
    /export_frames, either in Animations mode (`const uint32_t X[][5]`,
    binary on/off only) or Frames mode (`uint8_t X []`, full brightness
    levels). Recognized frames are appended to the current project.
    """
    content = (payload or {}).get('content', '')
    if not content or not content.strip():
        return {'error': 'no content provided'}

    on_value = max(1, BRIGHTNESS_LEVELS - 1)
    if '[][5]' in content:
        parsed = _parse_animation_header(content, on_value)
    else:
        parsed = _parse_frames_header(content)

    if not parsed:
        logger.warning("import_frames: no recognizable frames in uploaded file")
        return {'error': 'no recognizable frames found in file'}

    logger.info(f"Importing {len(parsed)} frame(s) from uploaded header")

    imported = []
    for item in parsed:
        frame = AppFrame(
            id=None,
            name=item['name'],
            position=None,
            duration_ms=item['duration_ms'],
            arr=np.array(item['rows'], dtype=np.uint8),
            brightness_levels=BRIGHTNESS_LEVELS,
        )
        frame.id = store.save_frame(frame)
        record = store.get_frame_by_id(frame.id)
        if record:
            frame = AppFrame.from_record(record)
        imported.append(frame)

    last = imported[-1]
    apply_frame_to_board(last)
    logger.info(f"Imported {len(imported)} frame(s), last id={last.id}")
    return {'ok': True, 'count': len(imported), 'frame': last.to_json(), 'vector': last.to_c_string()}



# English transliteration aliases, so frames named e.g. "final-kaf" or "Daled"
# (not just the literal Hebrew character) can still be matched to a letter.
# Each entry: (regular_index, final_index_or_None, [alias tokens]).
_LETTER_BASES = [
    (0, None, ["alef", "aleph"]),
    (1, None, ["bet", "vet", "beit", "bais"]),
    (2, None, ["gimel", "gimmel"]),
    (3, None, ["dalet", "daled", "daleth"]),
    (4, None, ["he", "hei", "heh"]),
    (5, None, ["vav", "vov", "waw"]),
    (6, None, ["zayin", "zain", "zayn"]),
    (7, None, ["het", "chet", "khet", "ches"]),
    (8, None, ["tet", "teth"]),
    (9, None, ["yod", "yud", "yodh"]),
    (11, 10, ["kaf", "chaf", "kaph"]),
    (12, None, ["lamed", "lamedh"]),
    (14, 13, ["mem"]),
    (16, 15, ["nun", "num"]),
    (17, None, ["samekh", "samech"]),
    (18, None, ["ayin", "ain", "ayn"]),
    (20, 19, ["pe", "fe", "peh"]),
    (22, 21, ["tsadi", "tzadi", "tzaddi", "tsadik", "tsadic", "tasdic", "tzadik"]),
    (23, None, ["qof", "kuf", "kof", "qoph"]),
    (24, None, ["resh", "reish"]),
    (25, None, ["shin", "sin"]),
    (26, None, ["tav", "taw", "sav"]),
]
_LETTER_ALIAS_LOOKUP = {
    alias: (regular_idx, final_idx)
    for regular_idx, final_idx, aliases in _LETTER_BASES
    for alias in aliases
}


def _match_letter_index(name: str) -> int | None:
    """Return the hebrewLetterToFrame slot (0..26) that ``name`` refers to.

    Tries a literal Hebrew character first (e.g. a frame named "ב"), then
    falls back to English transliteration aliases (e.g. "Bet", "final-kaf",
    "Daled"), detecting the final/sofit form via a "final"/"sofit" token.
    """
    if not name:
        return None
    for ch in name:
        idx = HEBREW_LETTERS_ORDER.find(ch)
        if idx != -1:
            return idx

    lowered = name.lower()
    is_final = 'final' in lowered or 'sofit' in lowered
    normalized = re.sub(r'[^a-z]', '', lowered).replace('final', '').replace('sofit', '')
    if not normalized:
        return None
    entry = _LETTER_ALIAS_LOOKUP.get(normalized)
    if entry is None:
        return None
    regular_idx, final_idx = entry
    if is_final:
        return final_idx if final_idx is not None else regular_idx
    return regular_idx


def upload_alphabet(payload: dict):
    """Replace the sketch's Animation.h with a new alphabet uploaded from the browser.

    Payload: {content: str} - text of a .h file (Animation or Frames mode, as
    produced by /export_frames) whose frame names/comments contain the Hebrew
    letter each frame represents (name a frame "ב" for Bet, etc. before
    exporting it). Frames are matched to hebrewLetterToFrame's 27 letters;
    unmatched/unnamed frames are dropped. Overwrites sketch/Animation.h, so
    the app must be restarted afterwards to rebuild and flash the sketch.
    """
    content = (payload or {}).get('content', '')
    if not content or not content.strip():
        return {'error': 'no content provided'}

    if '[][5]' in content:
        parsed = _parse_animation_header(content, on_value=255)
    else:
        parsed = _parse_frames_header(content)

    if not parsed:
        logger.warning("upload_alphabet: no recognizable frames in uploaded file")
        return {'error': 'no recognizable frames found in file'}

    by_letter = {}
    unmatched = 0
    for item in parsed:
        idx = _match_letter_index(item['name'])
        if idx is None:
            unmatched += 1
            continue
        by_letter[idx] = item  # last frame wins if a letter appears more than once

    if not by_letter:
        logger.warning("upload_alphabet: no frame name matched a Hebrew letter")
        return {'error': 'no frame names matched a Hebrew letter (name each frame with its letter, e.g. "ב", before exporting)'}

    ordered_letter_indices = sorted(by_letter.keys())
    mapping = [-1] * 27
    lines = ["const uint32_t animation[][5] = {"]
    for slot, letter_idx in enumerate(ordered_letter_indices):
        item = by_letter[letter_idx]
        mapping[letter_idx] = slot
        frame = AppFrame(
            id=None,
            name=item['name'],
            position=None,
            duration_ms=item['duration_ms'],
            arr=np.array(item['rows'], dtype=np.uint8),
            brightness_levels=BRIGHTNESS_LEVELS,
        )
        hex_str = ", ".join(frame.to_animation_hex())
        lines.append(f"    {{{hex_str}}},  // {HEBREW_LETTERS_ORDER[letter_idx]} ({HEBREW_LETTER_LABELS[letter_idx]})")
    lines.append("};")
    lines.append("")
    lines.append("// Index into animation[] for each Hebrew letter, in Unicode block order")
    lines.append("// (U+05D0 Alef .. U+05EA Tav, same order as sketch.ino's letter decoding). -1 = not drawn.")
    lines.append("const int8_t hebrewLetterToFrame[27] = {")
    for i, val in enumerate(mapping):
        lines.append(f"  {val}, // {i:2d}: {HEBREW_LETTERS_ORDER[i]} {HEBREW_LETTER_LABELS[i]}")
    lines.append("};")
    lines.append("")
    new_header = "\n".join(lines)

    try:
        SKETCH_ANIMATION_H.write_text(new_header, encoding="utf-8")
    except OSError as e:
        logger.error(f"upload_alphabet: failed to write {SKETCH_ANIMATION_H}: {e}")
        return {'error': f'could not write sketch/Animation.h: {e}'}

    missing = [HEBREW_LETTERS_ORDER[i] for i, v in enumerate(mapping) if v == -1]
    logger.info(f"upload_alphabet: matched {len(by_letter)}/27 letters, {unmatched} unmatched frame(s), missing={missing}")
    return {'ok': True, 'matched': len(by_letter), 'unmatched': unmatched, 'missing': missing}


def play_animation(payload: dict):
    """Play animation sequence on the board.

    Payload: {frames: [id,...], loop: bool}
    - frames: list of frame IDs to play in sequence
    """
    frame_ids = payload.get("frames", [])

    if not frame_ids:
        logger.warning("play_animation called with no frames")
        return {"error": "no frames provided"}

    # Check frame count against sketch buffer limit
    if len(frame_ids) > MAX_FRAMES:
        logger.error(f"Too many frames for animation: {len(frame_ids)} > {MAX_FRAMES}")
        return {"error": f"Animation exceeds maximum frame limit ({MAX_FRAMES} frames). Please reduce the number of frames."}

    logger.info(f"Playing animation: frame_count={len(frame_ids)}")

    # Load frames from DB
    records = [store.get_frame_by_id(fid) for fid in frame_ids]
    records = [r for r in records if r is not None]

    if not records:
        logger.warning("No valid frames found for animation")
        return {"error": "no valid frames found"}

    frames = [AppFrame.from_record(r) for r in records]
    logger.debug(f"Loaded {len(frames)} frames for animation")

    try:
        for f in frames:
            logger.debug(
                f"Frame id={f.id}, name='{f.name}', duration={f.duration_ms}ms"
            )
            [hex1, hex2, hex3, hex4, duration] = f.to_animation_hex()
            Bridge.notify(
                "load_frame",
                [
                    int(hex1, 16),
                    int(hex2, 16),
                    int(hex3, 16),
                    int(hex4, 16),
                    int(duration),
                ],
            )

        Bridge.call("play_animation")
        logger.info("play_animation called on board")

    except Exception as e:
        logger.warning(f"Failed to request play_animation: {e}")

    return {"ok": True, "frames_played": len(frames)}


def stop_animation():
    """Stop any running animation on the board.

    This endpoint calls the sketch provider `stop_animation`. No payload
    required.

    Returns:
        dict: {'ok': True} on success, {'error': str} on failure.
    """
    try:
        Bridge.call("stop_animation")
        logger.info("stop_animation called on board")
        return {'ok': True}
    except Exception as e:
        logger.warning(f"Failed to request stop_animation: {e}")
        return {'error': str(e)}


def write_sentence(payload: dict):
    """Spell out a Hebrew sentence on the LED matrix using pre-drawn letter frames.

    Payload: {text: str, loop: bool}

    The sketch (write_sentence provider) maps each Hebrew letter to a frame
    from Animation.h and plays them in sequence. Letters with no drawn frame
    (e.g. Alef) are silently skipped on the board. When loop is true, the
    sentence keeps scrolling repeatedly until stop_animation is called.
    """
    text = payload.get('text', '')
    loop = bool(payload.get('loop', False))
    if not text:
        return {'error': 'no text provided'}
    try:
        Bridge.call("set_scroll_loop", loop)
        Bridge.call("write_sentence", text)
        logger.info(f"write_sentence called on board: text={text!r}, loop={loop}")
        return {'ok': True}
    except Exception as e:
        logger.warning(f"Failed to request write_sentence: {e}")
        return {'error': str(e)}


ui.expose_api('POST', '/update_board', update_board)
ui.expose_api('POST', '/persist_frame', persist_frame)
ui.expose_api('POST', '/load_frame', load_frame)
ui.expose_api('GET', '/list_frames', list_frames)
ui.expose_api('POST', '/get_frame', get_frame)
ui.expose_api('POST', '/delete_frames', delete_frames)
ui.expose_api('POST', '/transform_frame', transform_frame)
ui.expose_api('POST', '/export_frames', export_frames)
ui.expose_api('POST', '/import_frames', import_frames)
ui.expose_api('POST', '/upload_alphabet', upload_alphabet)
ui.expose_api('POST', '/reorder_frames', reorder_frames)
ui.expose_api('POST', '/play_animation', play_animation)
ui.expose_api('POST', '/stop_animation', stop_animation)
ui.expose_api('POST', '/write_sentence', write_sentence)
ui.expose_api('GET', '/config', get_config)

App.run()
