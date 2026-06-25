"""
Рендер анимированного лидерборда АНТИДОБРОКЕК в MP4.

Логика и тайминги воспроизводят оригинальный HTML/CSS/JS файл максимально точно:
- ряды появляются снизу вверх (от последнего места к первому)
- бары растут по одному (scaleY 0->1, ease cubic-bezier(.22,.7,.2,1) ~ аппроксимируем)
- счётчик чисел анимируется синхронно с ростом баров (ease-out квадратичный)
- звук: тик на каждый бар (throttle 18мс), финальный "динь" когда #1 закончил
"""
from __future__ import annotations

import subprocess
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from dataclasses import dataclass
from pathlib import Path

from audio_gen import build_audio_track, SR

# ============================================================
# КОНСТАНТЫ (1:1 с оригинальным JS)
# ============================================================
W, H = 1920, 1080
FPS = 30

T_TITLE_IN = 0.700
T_PRE_BOARD = 0.450
T_NAME_IN = 0.450
T_BAR_STAGGER = 0.040
T_BAR_GROW = 0.600
T_BETWEEN_ROWS = 0.430
T_FOOTER_IN = 0.600
RECORD_END_HOLD_DEFAULT = 9.5

# П.1: бюджет времени на анимацию одного ряда (рост всех его баров, без учёта
# T_NAME_IN/T_BETWEEN_ROWS). Если при стандартном T_BAR_STAGGER ряд вышел бы
# длиннее этого бюджета — stagger для этого ряда пересчитывается меньшим,
# чтобы уложиться в лимит. Это не позволяет одному участнику с аномально
# большим count растягивать всё видео до неприличной длины.
MAX_ROW_DURATION = 6.0
# Нижняя граница пересчитанного stagger: тики в audio_gen.render_tick throttled
# на 18мс (`if now - lastTick < 0.018`), поэтому stagger короче этого порога
# означал бы, что часть тиков физически не звучит. 20мс - небольшой запас.
MIN_BAR_STAGGER = 0.020

RANK_COLORS = {
    1: (242, 183, 5),
    2: (214, 214, 214),
    3: (224, 133, 43),
    4: (138, 138, 138),
    5: (111, 111, 111),
}
RANK_FONT_SIZE = {1: 50, 2: 46, 3: 46, 4: 40, 5: 38}
DEFAULT_COLOR = RANK_COLORS[5]
DEFAULT_FONT_SIZE = 38

# П.2: защита от длинных имён - если label "{rank}. {name} — {count}" не
# влезает в LABEL_W даже после уменьшения шрифта до этого предела, текст
# обрезается с "…".
MIN_LABEL_FONT_SIZE = 24

BAR_W = 4
BAR_H = 54
BAR_GAP = 5
BAR_ROW_GAP = 7
BARS_ZONE_W = 900
LABEL_W = 470
LABEL_MARGIN_RIGHT = 46
ROW_GAP = 30
ROW_MIN_H = 54  # min-height строки = высота бара

# ---- Лимиты на отображение баров (универсальность при произвольном count) ----
# 2-5 место: показываем максимум 100 баров (1 строка), дальше "+"
BAR_CAP_OTHER_RANKS = 100
# 1 место: до 100 - как остальные, до 200 - 2 строки (как сейчас), до 300 - 3 строки
# (та же общая высота зоны, бар короче), дальше "+"
BAR_CAP_RANK1 = 300
# Порог перехода 1 места на 2 строки = round(BAR_CAP_RANK1 * 2/3) по умолчанию,
# рассчитывается динамически в Renderer.__init__ (см. bar_cap_rank1_two_rows).

# Зона под бары у 1 места жёстко фиксирована на высоте "2 обычных ряда" -
# при 3 строках бар становится короче, но сама зона не растёт, поэтому
# остальной layout (ROW_GAP, позиции других рядов) не меняется вообще.
RANK1_BARS_ZONE_H = 2 * BAR_H + BAR_ROW_GAP

PLUS_SIGN_COLOR_MATCHES_BAR = True  # "+" рисуется тем же цветом, что бары ряда
PLUS_SIGN_FONT_SIZE = 16  # уменьшено с 22 - заметно компактнее относительно высоты бара

HEADER_PADDING_TOP = 212
FOOTER_PADDING_BOTTOM = 148

ASSETS_DIR = Path(__file__).parent / "assets"
FONT_ONEST = str(ASSETS_DIR / "Onest-SemiBold.ttf")
FONT_ONEST_REGULAR = str(ASSETS_DIR / "Onest-Regular.ttf")
FONT_ONEST_EXTRABOLD = str(ASSETS_DIR / "Onest-ExtraBold.ttf")
FONT_EPISODE = str(ASSETS_DIR / "Ck_Blockhead.ttf")

TITLE_FONT_SIZE = 42  # уменьшено с 46 по корректировке
FOOTER_FONT_SIZE = 44  # размер не меняем, меняем только шрифт на ExtraBold

BG_PATHS = {
    "leaderboard": ASSETS_DIR / "bg-leaderboard.png",
    "heroes": ASSETS_DIR / "bg-heroes.png",
}

EP_LEFT, EP_TOP, EP_SIZE = 1467, 32, 120  # уменьшено с 130 по корректировке


# ============================================================
# EASING (соответствует CSS transitions)
# ============================================================
def ease_out_cubic_bezier_22_7_2_1(t: float) -> float:
    """Приближение cubic-bezier(.22,.7,.2,1) — используется для роста баров.
    Достаточно близкая визуально аппроксимация через ease-out с лёгким overshoot-like стартом."""
    t = max(0.0, min(1.0, t))
    # Симметричное приближение: быстрый старт, плавное замедление к 1
    return 1 - (1 - t) ** 2.2


def ease_linear_css(t: float) -> float:
    return max(0.0, min(1.0, t))


# ============================================================
# ДАННЫЕ
# ============================================================
@dataclass
class Participant:
    name: str
    count: int


@dataclass
class RowPlan:
    """Всё что нужно для рендера одного ряда + расписание его анимации (в секундах от начала видео)."""
    rank: int
    name: str
    count: int
    color: tuple
    font_size: int
    show_start: float  # когда ряд начинает появляться (label fade-in + bars start)
    bar_grow_starts: list  # время начала роста каждого бара (абсолютное, в секундах)
    count_start: float
    count_dur: float
    y: int  # вертикальная позиция ряда на канвасе (top)


@dataclass
class Timeline:
    rows: list  # list[RowPlan]
    header_show_at: float
    footer_show_at: float
    ding_at: float | None
    total_duration: float
    tick_times: list  # абсолютные времена всех тиков (до throttle, throttle применяется в audio_gen)


# ============================================================
# РАСЧЁТ ТАЙМЛАЙНА (повторяет play() из оригинального JS)
# ============================================================
def build_timeline(participants: list, board_center_y: int) -> Timeline:
    """participants должны быть уже отсортированы по count desc (rank 1 первый)."""
    n = len(participants)
    rows_height = n * ROW_MIN_H + (n - 1) * ROW_GAP if n > 0 else 0
    start_y = board_center_y - rows_height // 2

    row_plans = []
    tick_times = []
    ding_at = None

    header_show_at = 0.040
    t = T_TITLE_IN + T_PRE_BOARD

    # Оригинал идёт от последнего ряда (i = rows.length-1) к первому (i=0),
    # т.е. в порядке индексов rows массива (rank 1 = index 0) - снизу вверх по экрану.
    # rows[] отсортирован по убыванию count, значит rows[length-1] = последнее место (наименьший count).
    for i in range(n - 1, -1, -1):
        p = participants[i]
        rank = i + 1
        color = RANK_COLORS.get(rank, DEFAULT_COLOR)
        font_size = RANK_FONT_SIZE.get(rank, DEFAULT_FONT_SIZE)
        y = start_y + i * (ROW_MIN_H + ROW_GAP)

        # П.1: если рост всех баров ряда при стандартном stagger превысил бы
        # бюджет MAX_ROW_DURATION, пересчитываем stagger меньшим (но не ниже
        # MIN_BAR_STAGGER) - тики идут чаще, но длительность ряда укладывается
        # в лимит. Для небольших count (типичный случай) ничего не меняется.
        effective_stagger = T_BAR_STAGGER
        if p.count > 1:
            standard_row_anim_duration = (p.count - 1) * T_BAR_STAGGER + T_BAR_GROW
            if standard_row_anim_duration > MAX_ROW_DURATION:
                needed_stagger = (MAX_ROW_DURATION - T_BAR_GROW) / (p.count - 1)
                effective_stagger = max(MIN_BAR_STAGGER, needed_stagger)

        show_start = t
        bar_starts = [show_start + b * effective_stagger for b in range(p.count)]
        tick_times.extend(bar_starts)

        count_dur = (p.count - 1) * effective_stagger + T_BAR_GROW if p.count > 0 else 0.0
        count_start = show_start

        row_plans.append(RowPlan(
            rank=rank, name=p.name, count=p.count, color=color, font_size=font_size,
            show_start=show_start, bar_grow_starts=bar_starts,
            count_start=count_start, count_dur=count_dur, y=y,
        ))

        t += T_NAME_IN + (p.count * effective_stagger + T_BAR_GROW) + T_BETWEEN_ROWS

    footer_show_at = t
    # "Динь" синхронизирован с появлением футера (п.4.1) — а не с моментом
    # окончания роста баров 1 места, как было раньше (тогда звук опережал текст).
    ding_at = footer_show_at if n > 0 else None
    total_duration = t + T_FOOTER_IN + 0.200

    return Timeline(
        rows=row_plans, header_show_at=header_show_at, footer_show_at=footer_show_at,
        ding_at=ding_at, total_duration=total_duration, tick_times=tick_times,
    )


# ============================================================
# РЕНДЕР ОДНОГО КАДРА
# ============================================================
class Renderer:
    def __init__(self, mode: str, title: str, footer: str, episode: str, show_title: bool = True,
                 bar_cap_other_ranks: int = BAR_CAP_OTHER_RANKS, bar_cap_rank1: int = BAR_CAP_RANK1):
        self.mode = mode
        self.title = title
        self.footer = footer
        self.episode = episode
        self.show_title = show_title
        # П.3: пороги отображения баров теперь настраиваемые (раньше были константами).
        self.bar_cap_other_ranks = bar_cap_other_ranks
        self.bar_cap_rank1 = bar_cap_rank1
        # Порог перехода 1 места на 2 строки масштабируется пропорционально
        # bar_cap_rank1, сохраняя ту же пропорцию, что было у дефолтных 100/200/300
        # (т.е. 1/3 и 2/3 от общего предела), чтобы три ступени (1/2/3 строки)
        # оставались согласованными при изменении bar_cap_rank1 через CLI.
        self.bar_cap_rank1_two_rows = round(bar_cap_rank1 * 2 / 3)
        self.bg = Image.open(BG_PATHS[mode]).convert("RGB")
        if self.bg.size != (W, H):
            self.bg = self.bg.resize((W, H))

        self._font_cache = {}
        self._glyph_bbox_cache = {}
        self._label_fit_cache = {}

    def _font(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = (path, size)
        if key not in self._font_cache:
            self._font_cache[key] = ImageFont.truetype(path, size)
        return self._font_cache[key]

    def _draw_label_right_aligned(self, draw: ImageDraw.ImageDraw, box_right_x: int, box_center_y: int,
                                   text: str, color: tuple, font_size: int, opacity: float, y_offset: float):
        if opacity <= 0:
            return
        font = self._font(FONT_ONEST, font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = box_right_x - tw - bbox[0]
        y = box_center_y - th / 2 - bbox[1] + y_offset
        a = max(0, min(255, int(round(opacity * 255))))
        fill = color + (a,)
        # Рисуем через отдельный RGBA layer для честной альфы (PIL ImageDraw.text на RGB канвасе не умеет alpha blend)
        return (x, y, font, fill)

    def _fit_label_text(self, draw: ImageDraw.ImageDraw, rank: int, name: str, count: int,
                         base_font_size: int, max_width: int) -> tuple:
        """П.2: подбирает (font, display_name) так, чтобы текст "{rank}. {name} — {count}"
        влезал в max_width. Сначала уменьшает font_size (не ниже MIN_LABEL_FONT_SIZE),
        затем, если и на минимальном размере не влезает, обрезает ИМЯ (не число) с "…".
        Возвращает (font, display_name) - display_name может быть обрезанной версией name,
        которую следует использовать при формировании текста для любого момента анимации
        этого ряда (в т.ч. с промежуточным, ещё не финальным, count).
        Результат кэшируется (не зависит от t, дорого пересчитывать на каждом кадре)."""
        cache_key = (rank, name, count, base_font_size, max_width)
        if cache_key in self._label_fit_cache:
            return self._label_fit_cache[cache_key]

        font_size = base_font_size
        text = f"{rank}. {name} — {count}"
        font = self._font(FONT_ONEST, font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]

        while tw > max_width and font_size > MIN_LABEL_FONT_SIZE:
            font_size -= 1
            font = self._font(FONT_ONEST, font_size)
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]

        if tw <= max_width:
            result = (font, name)
            self._label_fit_cache[cache_key] = result
            return result

        # На минимальном размере всё ещё не влезает - обрезаем ИМЯ с "…"
        # (число и "rank. " не трогаем, т.к. это значимая числовая информация)
        truncated_name = name
        while len(truncated_name) > 1:
            truncated_name = truncated_name[:-1]
            candidate_name = truncated_name.rstrip() + "…"
            candidate_text = f"{rank}. {candidate_name} — {count}"
            bbox = draw.textbbox((0, 0), candidate_text, font=font)
            tw = bbox[2] - bbox[0]
            if tw <= max_width:
                result = (font, candidate_name)
                self._label_fit_cache[cache_key] = result
                return result
        result = (font, truncated_name.rstrip() + "…")
        self._label_fit_cache[cache_key] = result
        return result

    def render_frame(self, tl: Timeline, t: float) -> Image.Image:
        canvas = self.bg.copy().convert("RGBA")
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # ---- Header (заголовок). П.2.2: в режиме heroes заголовок убран — он уже вшит в новый bg-heroes.png ----
        if self.show_title:
            header_progress = self._fade_progress(t, tl.header_show_at, T_TITLE_IN)
            if header_progress > 0:
                font = self._font(FONT_ONEST, TITLE_FONT_SIZE)  # п.1.1: 46px (было 42)
                text = self.title.upper()
                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                y_offset = (1 - header_progress) * 14  # translateY(14px) -> 0
                x = (W - tw) / 2 - bbox[0]
                y = HEADER_PADDING_TOP - bbox[1] + y_offset
                a = int(round(header_progress * 255))
                draw.text((x, y), text, font=font, fill=(243, 243, 246, a))

        # ---- Episode number (только в leaderboard режиме) ----
        if self.mode == "leaderboard":
            font = self._font(FONT_EPISODE, EP_SIZE)
            bbox = draw.textbbox((0, 0), self.episode, font=font)
            y = EP_TOP + (94 - (bbox[3] - bbox[1])) / 2 - bbox[1]
            draw.text((EP_LEFT, y), self.episode, font=font, fill=(230, 253, 70, 255))

        # ---- Rows ----
        label_x0 = (W - (LABEL_W + LABEL_MARGIN_RIGHT + BARS_ZONE_W)) // 2
        label_right = label_x0 + LABEL_W
        bars_x0 = label_x0 + LABEL_W + LABEL_MARGIN_RIGHT

        for row in tl.rows:
            row_progress = self._fade_progress(t, row.show_start, T_NAME_IN)
            if row_progress <= 0:
                continue

            # Label (имя + число). Шрифт/обрезка ИМЕНИ подбираются по ФИНАЛЬНОМУ
            # count, а не по текущему промежуточному - иначе размер шрифта мог бы
            # чуть "дрожать" по ходу анимации счётчика. Если имя пришлось обрезать,
            # та же обрезанная версия используется для текста на любом кадре.
            current_count = self._current_count(t, row)
            font, display_name = self._fit_label_text(draw, row.rank, row.name, row.count, row.font_size, LABEL_W)
            label_text = f"{row.rank}. {display_name} — {current_count}"
            bbox = draw.textbbox((0, 0), label_text, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            y_offset = (1 - row_progress) * 16
            x = label_right - tw - bbox[0]
            y = row.y + (ROW_MIN_H - th) / 2 - bbox[1] + y_offset
            a = int(round(row_progress * 255))
            draw.text((x, y), label_text, font=font, fill=row.color + (a,))

            # Bars
            self._draw_bars(draw, row, bars_x0, t)

        # ---- Footer ----
        footer_progress = self._fade_progress(t, tl.footer_show_at, T_FOOTER_IN)
        if footer_progress > 0:
            font = self._font(FONT_ONEST_EXTRABOLD, FOOTER_FONT_SIZE)  # п.1.1/2.3: шрифт ExtraBold, размер тот же
            bbox = draw.textbbox((0, 0), self.footer, font=font)
            tw = bbox[2] - bbox[0]
            y_offset = (1 - footer_progress) * 14
            x = (W - tw) / 2 - bbox[0]
            y = H - FOOTER_PADDING_BOTTOM - bbox[3] + y_offset
            a = int(round(footer_progress * 255))
            draw.text((x, y), self.footer, font=font, fill=(255, 255, 255, a))

        canvas = Image.alpha_composite(canvas, overlay)
        return canvas.convert("RGB")

    @staticmethod
    def _fade_progress(t: float, start: float, dur: float) -> float:
        if t < start:
            return 0.0
        return min(1.0, (t - start) / dur)

    @staticmethod
    def _current_count(t: float, row: RowPlan) -> int:
        """Дискретный счётчик: число равно количеству баров, чей старт роста уже наступил.
        Это гарантирует точную синхронизацию звука тика и смены цифры (п.4.1) —
        в отличие от прежней гладкой ease-out кривой, которая расходилась с тиками
        к концу анимации (на больших count тик отрабатывал раньше смены числа)."""
        if row.count <= 0:
            return 0
        if t < row.show_start:
            return 0
        # bar_grow_starts строго возрастает, можно искать через бинарный поиск,
        # но count обычно небольшой (десятки) - линейный проход дешевле и проще.
        n = 0
        for bar_start in row.bar_grow_starts:
            if t >= bar_start:
                n += 1
            else:
                break
        return n

    def _bar_cap_for_rank(self, rank: int) -> int:
        return self.bar_cap_rank1 if rank == 1 else self.bar_cap_other_ranks

    def _bars_display_params(self, rank: int, visible_count: int) -> tuple:
        """Возвращает (bars_per_row, bar_h, n_rows) для отрисовки `visible_count` баров
        этого ранга. visible_count уже ограничен кэпом (т.е. <= cap для этого ранга)."""
        bars_per_row_normal = max(1, (BARS_ZONE_W + BAR_GAP) // (BAR_W + BAR_GAP))

        if rank != 1 or visible_count <= self.bar_cap_other_ranks:
            # Обычный случай: 2-5 место (всегда <=cap_other_ranks, 1 строка),
            # либо 1 место с <=cap_other_ranks
            return bars_per_row_normal, BAR_H, 1

        if visible_count <= self.bar_cap_rank1_two_rows:
            # 1 место, cap_other_ranks < count <= cap_rank1_two_rows: 2 строки обычной высоты
            return bars_per_row_normal, BAR_H, 2

        # 1 место, дальше до bar_cap_rank1: 3 строки, бар короче, но та же общая зона высоты
        bar_h_small = (RANK1_BARS_ZONE_H - 2 * BAR_ROW_GAP) / 3
        return bars_per_row_normal, bar_h_small, 3

    def _glyph_pixel_bbox(self, text: str, font: ImageFont.FreeTypeFont) -> tuple:
        """Реальный bbox закрашенных пикселей глифа (не метрики шрифта из textbbox,
        которые часто включают невидимые отступы дизайнера и не совпадают с видимым
        контуром символа - именно это давало неточное центрирование "+" ранее).
        Возвращает (left, top, right, bottom) относительно точки рисования (0,0)."""
        key = (text, font.path if hasattr(font, "path") else id(font), font.size)
        if key in self._glyph_bbox_cache:
            return self._glyph_bbox_cache[key]
        # Рисуем на достаточно большом холсте с отступом, чтобы не отрезать выносные части
        pad = font.size * 2
        probe = Image.new("L", (font.size * 4, font.size * 4), 0)
        pdraw = ImageDraw.Draw(probe)
        pdraw.text((pad, pad), text, font=font, fill=255)
        bbox = probe.getbbox()
        if bbox is None:
            bbox = (pad, pad, pad, pad)
        # переводим обратно в координаты относительно (0,0) точки рисования
        result = (bbox[0] - pad, bbox[1] - pad, bbox[2] - pad, bbox[3] - pad)
        self._glyph_bbox_cache[key] = result
        return result

    def _draw_bars(self, draw: ImageDraw.ImageDraw, row: RowPlan, bars_x0: int, t: float):
        cap = self._bar_cap_for_rank(row.rank)
        visible_count = min(row.count, cap)
        overflow = row.count > cap  # нужно ли рисовать "+"

        # Геометрия (n_rows, bar_h) считается от ФИНАЛЬНОГО count ряда, а не от
        # текущего видимого числа в моменте t. Иначе при переходе через 100/200
        # бары, выросшие до этого момента, резко меняли бы высоту одним кадром -
        # геометрия должна быть известна и зафиксирована с первого же бара ряда.
        bars_per_row, bar_h, n_rows = self._bars_display_params(row.rank, visible_count)
        strip_h = n_rows * bar_h + (n_rows - 1) * BAR_ROW_GAP
        bottom_y = row.y + ROW_MIN_H  # низ зоны баров совпадает с низом строки
        top_y = bottom_y - strip_h

        placed = 0
        cur_row = 0
        while placed < visible_count:
            row_y = top_y + cur_row * (bar_h + BAR_ROW_GAP)
            bx = bars_x0
            for _ in range(bars_per_row):
                if placed >= visible_count:
                    break
                bar_start = row.bar_grow_starts[placed]
                progress = self._fade_progress(t, bar_start, T_BAR_GROW)
                if progress > 0:
                    scale_y = ease_out_cubic_bezier_22_7_2_1(progress)
                    bar_h_now = bar_h * scale_y
                    bar_top = row_y + (bar_h - bar_h_now)  # растёт снизу вверх
                    op_progress = self._fade_progress(t, bar_start, 0.4)
                    a = int(round(op_progress * 255))
                    if bar_h_now > 0.5:
                        draw.rectangle(
                            [bx, bar_top, bx + BAR_W, row_y + bar_h],
                            fill=row.color + (a,),
                        )
                bx += BAR_W + BAR_GAP
                placed += 1
            cur_row += 1

        if overflow:
            # "+" появляется ровно в момент, когда должен был бы вырасти бар №(cap+1),
            # если бы лимита не было - т.е. строго синхронно с тем, как счётчик
            # впервые превышает cap (звук тика на этот момент уже звучит независимо
            # от кэпа, см. build_timeline/tick_times).
            plus_start = row.bar_grow_starts[cap] if cap < len(row.bar_grow_starts) else row.bar_grow_starts[-1]
            plus_progress = self._fade_progress(t, plus_start, T_BAR_GROW)
            if plus_progress > 0:
                # Позиция "+": сразу после последнего нарисованного бара последней
                # ЗАНЯТОЙ строки. last_row_idx считается от фактического размещения
                # visible_count баров по bars_per_row штук - НЕ от n_rows-1, т.к.
                # при произвольных (настраиваемых через CLI) cap-порогах n_rows может
                # не совпадать с реально занятым количеством строк (например, при
                # bar_cap_rank1=150 и bars_per_row=100 третья строка вмещает 150-200=-50,
                # т.е. на деле занято только 2 строки, хотя геометрия задаёт n_rows=3).
                last_row_idx = max(0, (visible_count - 1) // bars_per_row) if visible_count > 0 else 0
                last_row_y = top_y + last_row_idx * (bar_h + BAR_ROW_GAP)
                bars_in_last_row = visible_count - last_row_idx * bars_per_row
                # Дополнительный отступ от последнего бара (помимо обычного BAR_GAP
                # между барами), чтобы "+" визуально читался как отдельный элемент,
                # а не как "ещё один бар" в ряду - раньше он стоял слишком близко.
                PLUS_EXTRA_GAP = 1  # подтверждено пользователем как итоговое значение
                plus_x = bars_x0 + bars_in_last_row * (BAR_W + BAR_GAP) + PLUS_EXTRA_GAP

                # "+" рисуется ГЕОМЕТРИЧЕСКИ (два прямоугольника одинаковой толщины),
                # а не шрифтом. У глифа "+" в Onest-SemiBold горизонтальная и
                # вертикальная палочки разной толщины (особенность дизайна шрифта).
                # ВАЖНО: все координаты округляются до целых пикселей ДО вызова
                # rectangle. PIL рисует прямоугольники с float-координатами через
                # антиалиасинг по краям, и при нецелых cross_cx/cross_cy/half_t
                # (а bar_h/plus_x сами по себе нередко дробные - например
                # bar_h=33.67 у сжатого варианта 1 места) горизонтальная и
                # вертикальная полосы попадают на разную субпиксельную позицию,
                # из-за чего после растеризации получаются заметно разной
                # толщины - именно это давало "кривой" крест ранее, даже когда
                # геометрические размеры обеих полос были идентичны в float.
                cross_size = PLUS_SIGN_FONT_SIZE  # общий размер креста (сторона)
                cross_thickness = max(2, round(cross_size * 0.16))  # тоньше: было 0.22
                # толщина чётная, чтобы half_t было целым без округления в обе стороны
                if cross_thickness % 2 != 0:
                    cross_thickness += 1
                cross_cx = round(plus_x + cross_size / 2)
                cross_cy = round(last_row_y + bar_h / 2)
                a = int(round(plus_progress * 255))
                color = row.color if PLUS_SIGN_COLOR_MATCHES_BAR else (255, 255, 255)
                fill = color + (a,)
                half = round(cross_size / 2)
                half_t = cross_thickness // 2
                # горизонтальная палочка
                draw.rectangle(
                    [cross_cx - half, cross_cy - half_t, cross_cx + half, cross_cy + half_t],
                    fill=fill,
                )
                # вертикальная палочка
                draw.rectangle(
                    [cross_cx - half_t, cross_cy - half, cross_cx + half_t, cross_cy + half],
                    fill=fill,
                )


# ============================================================
# П.5: ПРОВЕРКА КОЛЛИЗИЙ LAYOUT
# ============================================================
def _check_layout_collisions(tl: Timeline, show_title: bool) -> None:
    """Предупреждает в консоль (не прерывает рендер), если расчётные границы
    блока рядов пересекаются с зоной заголовка или футера при выбранном
    board_center_y. Учитывает возможный 3-рядный сжатый вариант баров у 1
    места (через RANK1_BARS_ZONE_H, которая >= обычной высоты строки).
    Границы заголовка/футера считаются по РЕАЛЬНЫМ метрикам шрифта (не грубым
    приближением), чтобы не давать ложных срабатываний на стандартном layout."""
    if not tl.rows:
        return

    # Верхняя/нижняя граница блока рядов на экране. row.y - это top строки;
    # высота зоны под бары у 1 места может быть больше ROW_MIN_H (RANK1_BARS_ZONE_H),
    # поэтому берём максимум из них для надёжной оценки верхней границы.
    top_row = min(tl.rows, key=lambda r: r.y)
    bottom_row = max(tl.rows, key=lambda r: r.y)
    rows_top = top_row.y - max(0, RANK1_BARS_ZONE_H - ROW_MIN_H)
    rows_bottom = bottom_row.y + ROW_MIN_H

    warnings = []

    # Точная высота строки заголовка/футера через реальные метрики шрифта,
    # а не грубое приближение (которое давало ложные срабатывания на стандартном
    # layout - например, разница в 6px между оценкой и фактом).
    probe_img = Image.new("RGBA", (10, 10))
    probe_draw = ImageDraw.Draw(probe_img)

    if show_title:
        title_font = ImageFont.truetype(FONT_ONEST, TITLE_FONT_SIZE)
        sample_bbox = probe_draw.textbbox((0, 0), "ТОП АНТИДОБРОКЕКЕРОВ ВЫПУСКА", font=title_font)
        title_line_h = sample_bbox[3] - sample_bbox[1]
        header_zone_bottom = HEADER_PADDING_TOP + title_line_h
        if rows_top < header_zone_bottom:
            warnings.append(
                f"верхняя граница блока рядов (Y={rows_top}) пересекается с зоной "
                f"заголовка (занимает Y до ~{header_zone_bottom}). Рекомендуется "
                f"увеличить board_center_y."
            )

    footer_font = ImageFont.truetype(FONT_ONEST_EXTRABOLD, FOOTER_FONT_SIZE)
    sample_bbox = probe_draw.textbbox((0, 0), "ВСЕМ СПАСИБО ЗА ВИДЕО", font=footer_font)
    footer_line_h = sample_bbox[3] - sample_bbox[1]
    footer_zone_top = H - FOOTER_PADDING_BOTTOM - footer_line_h
    if rows_bottom > footer_zone_top:
        warnings.append(
            f"нижняя граница блока рядов (Y={rows_bottom}) пересекается с зоной "
            f"футера (занимает Y от ~{footer_zone_top}). Рекомендуется "
            f"уменьшить board_center_y."
        )

    for w in warnings:
        print(f"[ВНИМАНИЕ] Layout: {w}", file=sys.stderr)


# ============================================================
# ВЫСОКОУРОВНЕВАЯ ФУНКЦИЯ РЕНДЕРА ВИДЕО (pipe в ffmpeg)
# ============================================================
def render_video(
    participants: list,
    mode: str,
    title: str,
    footer: str,
    episode: str,
    output_path: str,
    sound: bool = True,
    board_center_y: int = 520,  # было 620; -100px по п.3 правок
    show_title: bool = True,
    end_hold: float | None = None,  # None = RECORD_END_HOLD по умолчанию (leaderboard); для heroes передаём 5.0
    bar_cap_other_ranks: int = BAR_CAP_OTHER_RANKS,  # П.3: настраиваемые пороги отображения баров
    bar_cap_rank1: int = BAR_CAP_RANK1,
    sound_gain: float = 1.0,  # П.6: глобальный множитель громкости звука
):
    sorted_p = sorted(participants, key=lambda p: -p.count)
    tl = build_timeline(sorted_p, board_center_y)
    hold = end_hold if end_hold is not None else RECORD_END_HOLD_DEFAULT
    # П.5: для heroes видео должно заканчиваться через 5с после "диня"/футера,
    # а не после полного RECORD_END_HOLD (9.5с) как в leaderboard.
    total_duration = tl.footer_show_at + hold if end_hold is not None else tl.total_duration + hold
    n_frames = int(round(total_duration * FPS))

    # П.5: проверка коллизий layout - предупреждаем, если блок рядов выходит
    # за пределы зоны заголовка/футера при выбранном board_center_y.
    _check_layout_collisions(tl, show_title)

    renderer = Renderer(
        mode=mode, title=title, footer=footer, episode=episode, show_title=show_title,
        bar_cap_other_ranks=bar_cap_other_ranks, bar_cap_rank1=bar_cap_rank1,
    )

    video_only_path = str(output_path) + ".video_only.mp4"
    audio_path = str(output_path) + ".audio.wav"
    passlog_path = str(output_path) + ".ffmpeg2pass"

    # П.6: максимальное качество — 2-pass libx264 VBR при высоком целевом битрейте.
    # Контент простой (статичный фон + текст/бары), но 2 прохода всё равно дают
    # более стабильное распределение бит, чем однопроходный CRF, плюс высокий
    # битрейт сам по себе исключает видимые артефакты сжатия.
    # Так как кадры идут через pipe в stdin (не перемотать), для 2 проходов
    # генерируем кадры дважды — дороже по времени рендера PIL, но не по диску.
    TARGET_BITRATE = "12M"
    MAXRATE = "16M"
    BUFSIZE = "24M"

    def _generate_frames(proc: subprocess.Popen):
        for frame_idx in range(n_frames):
            t = frame_idx / FPS
            img = renderer.render_frame(tl, t)
            arr = np.asarray(img, dtype=np.uint8)
            proc.stdin.write(arr.tobytes())
        proc.stdin.close()
        proc.wait()

    null_device = "NUL" if sys.platform.startswith("win") else "/dev/null"
    pass1_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-preset", "veryslow",
        "-b:v", TARGET_BITRATE, "-maxrate", MAXRATE, "-bufsize", BUFSIZE,
        "-pix_fmt", "yuv420p",
        "-pass", "1", "-passlogfile", passlog_path,
        "-f", "mp4",
        null_device,
    ]
    proc1 = subprocess.Popen(pass1_cmd, stdin=subprocess.PIPE)
    _generate_frames(proc1)

    pass2_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-an",
        "-c:v", "libx264", "-preset", "veryslow",
        "-b:v", TARGET_BITRATE, "-maxrate", MAXRATE, "-bufsize", BUFSIZE,
        "-pix_fmt", "yuv420p",
        "-pass", "2", "-passlogfile", passlog_path,
        "-movflags", "+faststart",
        video_only_path,
    ]
    proc2 = subprocess.Popen(pass2_cmd, stdin=subprocess.PIPE)
    _generate_frames(proc2)

    try:
        if sound:
            audio = build_audio_track(total_duration, tl.tick_times, tl.ding_at, gain_multiplier=sound_gain)
            _write_wav(audio_path, audio)

            mux_cmd = [
                "ffmpeg", "-y",
                "-i", video_only_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-shortest",
                str(output_path),
            ]
            subprocess.run(mux_cmd, check=True)
        else:
            # ещё нужно добавить тихую AAC дорожку для совместимости с concat в build_video.py
            mux_cmd = [
                "ffmpeg", "-y",
                "-i", video_only_path,
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                "-shortest",
                str(output_path),
            ]
            subprocess.run(mux_cmd, check=True)
    finally:
        # Чистим промежуточные файлы независимо от исхода муксинга
        cleanup_paths = [video_only_path, audio_path,
                          passlog_path + "-0.log", passlog_path + "-0.log.mbtree"]
        for p in cleanup_paths:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass

    return output_path


def _write_wav(path: str, audio: np.ndarray):
    import wave
    pcm16 = (audio * 32767).astype(np.int16)
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm16.tobytes())
