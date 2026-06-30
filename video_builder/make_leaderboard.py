#!/usr/bin/env python3
"""
CLI для рендера анимированного лидерборда / героев мемной паузы.

Примеры:
    poetry run python make_leaderboard.py --mode leaderboard --episode 16 \\
        --title "Топ антидоброкекеров выпуска" --footer "ВСЕМ СПАСИБО ЗА ВИДЕО!" \\
        --participant "Максим:59" --participant "Ilya:33" --participant "Daniil:28" \\
        --participant "Александр:15" --participant "Sasha:12" \\
        --output output/outro.mp4

    poetry run python make_leaderboard.py --mode heroes --episode 16 \\
        --footer "СПАСИБО ЗА МЕМЫ!" \\
        --participant "Александр:32" --participant "Ilya:5" \\
        --output output/meme_heroes.mp4

В режиме heroes заголовок не рисуется (он уже вшит в bg-heroes.png),
поэтому --title необязателен и игнорируется. Видео в этом режиме автоматически
обрывается через 5 секунд после появления футера/диня (п.5 правок).

Без звука (для быстрой проверки геометрии):
    ... --no-sound

П.7: режим --mode both рендерит ОБА видео одним запуском - leaderboard и heroes.
Логика каждого из них не меняется (это два независимых вызова render_video под
капотом, как если бы ты запустил скрипт два раза отдельно) - просто используется
один общий --output как директория, куда кладутся outro.mp4 и meme_heroes.mp4.
Для leaderboard-части используются --title/--footer/--participant как обычно;
для heroes-части - --hero-footer/--hero-participant (отдельный набор участников):

    poetry run python make_leaderboard.py --mode both --episode 16 \\
        --title "Топ антидоброкекеров выпуска" --footer "ВСЕМ СПАСИБО ЗА ВИДЕО!" \\
        --participant "Максим:59" --participant "Ilya:33" \\
        --hero-footer "СПАСИБО ЗА МЕМЫ!" \\
        --hero-participant "Александр:32" --hero-participant "Ilya:5" \\
        --output output/
"""
import argparse
import sys
from pathlib import Path

from leaderboard_render import render_video, Participant, BAR_CAP_OTHER_RANKS, BAR_CAP_RANK1

HEROES_END_HOLD = 5.0  # п.5: heroes заканчивается через 5с после footer/динь, а не через 9.5с


def parse_participant(s: str) -> Participant:
    if ":" not in s:
        raise argparse.ArgumentTypeError(f"Ожидается формат 'Имя:количество', получено: {s}")
    name, count_str = s.rsplit(":", 1)
    try:
        count = int(count_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Количество должно быть числом: {s}")
    return Participant(name=name.strip(), count=count)


def _render_one(mode: str, title: str, footer: str, episode: str, participants: list,
                 output_path: str, args) -> str:
    """Общий путь рендера одного видео (leaderboard ИЛИ heroes) - используется
    и при --mode leaderboard/heroes напрямую, и дважды при --mode both (п.7),
    чтобы логика каждого режима оставалась идентичной независимому запуску."""
    show_title = mode == "leaderboard"
    end_hold = HEROES_END_HOLD if mode == "heroes" else None

    print(f"Режим: {mode}, выпуск #{episode or '—'}, участников: {len(participants)}")
    for p in participants:
        print(f"  {p.name}: {p.count}")

    out = render_video(
        participants=participants,
        mode=mode,
        title=title,
        footer=footer,
        episode=episode,
        output_path=output_path,
        sound=not args.no_sound,
        board_center_y=args.board_center_y,
        show_title=show_title,
        end_hold=end_hold,
        bar_cap_other_ranks=args.bar_cap_others,
        bar_cap_rank1=args.bar_cap_rank1,
        sound_gain=args.sound_gain,
    )
    print(f"Готово: {out}")
    return out


def main():
    parser = argparse.ArgumentParser(description="Рендер анимированного лидерборда АНТИДОБРОКЕК")
    parser.add_argument("--mode", choices=["leaderboard", "heroes", "both"], required=True)
    parser.add_argument("--episode", default="", help="Номер выпуска (используется только в leaderboard режиме)")
    parser.add_argument(
        "--title", default="",
        help="Заголовок (только для leaderboard; в heroes игнорируется — заголовок вшит в фон)",
    )
    parser.add_argument("--footer", default="", help="Нижняя строка для leaderboard (или для heroes, если не --mode both)")
    parser.add_argument(
        "--participant", action="append", dest="participants",
        type=parse_participant, metavar="ИМЯ:КОЛИЧЕСТВО",
        help="Участник в формате 'Имя:количество'. Можно указывать несколько раз. "
             "Для --mode both это участники leaderboard-части.",
    )
    # П.7: отдельные аргументы для heroes-части при --mode both
    parser.add_argument(
        "--hero-footer", default="",
        help="Нижняя строка для heroes-части (используется только при --mode both)",
    )
    parser.add_argument(
        "--hero-participant", action="append", dest="hero_participants",
        type=parse_participant, metavar="ИМЯ:КОЛИЧЕСТВО",
        help="Участник героев мемной паузы (используется только при --mode both)",
    )
    parser.add_argument(
        "--output", required=True,
        help="Путь к выходному mp4 (--mode leaderboard/heroes), "
             "либо к выходной директории (--mode both, туда кладутся outro.mp4 и meme_heroes.mp4)",
    )
    parser.add_argument("--no-sound", action="store_true", help="Отключить звук (тики/динь)")
    parser.add_argument(
        "--board-center-y", type=int, default=520,
        help="Вертикальный центр блока рядов на канвасе (по умолчанию 520; сдвинуто на -100px согласно п.3 правок)",
    )
    # П.3: настраиваемые пороги отображения баров
    parser.add_argument(
        "--bar-cap-others", type=int, default=BAR_CAP_OTHER_RANKS,
        help=f"Максимум видимых баров у 2-5 места, дальше '+' (по умолчанию {BAR_CAP_OTHER_RANKS})",
    )
    parser.add_argument(
        "--bar-cap-rank1", type=int, default=BAR_CAP_RANK1,
        help=f"Максимум видимых баров у 1 места, дальше '+' (по умолчанию {BAR_CAP_RANK1})",
    )
    # П.6: глобальная громкость звука
    parser.add_argument(
        "--sound-gain", type=float, default=1.0,
        help="Множитель громкости тиков/диня (по умолчанию 1.0). Полезно при "
             "сведении с фоновой музыкой на финальной склейке.",
    )

    args = parser.parse_args()

    if args.mode in ("leaderboard", "heroes"):
        if not args.participants:
            print("Ошибка: нужен хотя бы один --participant", file=sys.stderr)
            sys.exit(1)

    if args.mode == "leaderboard" and not args.title:
        print("Ошибка: для режима leaderboard нужен --title", file=sys.stderr)
        sys.exit(1)

    if args.mode in ("leaderboard", "heroes") and not args.footer:
        print("Ошибка: нужен --footer", file=sys.stderr)
        sys.exit(1)

    if args.mode == "both":
        # П.7: два независимых вызова, каждый идёт по тому же пути, что и
        # обычный --mode leaderboard / --mode heroes - логика не дублируется
        # и не меняется, см. _render_one().
        if not args.title:
            print("Ошибка: для --mode both нужен --title (для leaderboard-части)", file=sys.stderr)
            sys.exit(1)
        if not args.footer:
            print("Ошибка: для --mode both нужен --footer (для leaderboard-части)", file=sys.stderr)
            sys.exit(1)
        if not args.participants:
            print("Ошибка: для --mode both нужен хотя бы один --participant (leaderboard-часть)", file=sys.stderr)
            sys.exit(1)
        if not args.hero_footer:
            print("Ошибка: для --mode both нужен --hero-footer (heroes-часть)", file=sys.stderr)
            sys.exit(1)
        if not args.hero_participants:
            print("Ошибка: для --mode both нужен хотя бы один --hero-participant (heroes-часть)", file=sys.stderr)
            sys.exit(1)

        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)

        print("=== Рендер leaderboard-части ===")
        _render_one(
            mode="leaderboard", title=args.title, footer=args.footer, episode=args.episode,
            participants=args.participants, output_path=str(out_dir / "outro.mp4"), args=args,
        )
        print("=== Рендер heroes-части ===")
        _render_one(
            mode="heroes", title="", footer=args.hero_footer, episode=args.episode,
            participants=args.hero_participants, output_path=str(out_dir / "meme_heroes.mp4"), args=args,
        )
    else:
        _render_one(
            mode=args.mode, title=args.title, footer=args.footer, episode=args.episode,
            participants=args.participants, output_path=args.output, args=args,
        )


if __name__ == "__main__":
    main()
