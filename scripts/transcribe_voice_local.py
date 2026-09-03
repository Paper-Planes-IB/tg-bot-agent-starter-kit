#!/usr/bin/env python3
"""Local Telegram voice transcription helper.

Prefers mlx-whisper on Apple Silicon and falls back to faster-whisper.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

DEFAULT_PROMPT = (
    "Toshkent Gullari, TG Dashboard, Flowers, Nour, Plants, Wedding, E-com, Marketplace, "
    "Gourmet, Guul, School, B2B Opt, Corp, Yacom. "
    "Мухассар, Муссара, Яком, Нур, Гуул, Гурме, Плэнтс, Веддинг, Иком, Маркетплейс. "
    "Выручка, оборот, чеки, средний чек, LCPQ, RFM, amoCRM, OX, брак, списание, "
    "задача, напоминание, сверить, проверить, дашборд, воронка, лиды. "
    "Toshkent, O'zbekiston, o'zbekcha, vazifa, eslatma, tekshirish, daromad, tushum, "
    "chek, o'rtacha chek, savdo, mijoz, buyurtma, hisobot, nuqson, hisobdan chiqarish."
)


def read_prompt(prompt: str, prompt_file: Path | None) -> str:
    if prompt_file:
        return prompt_file.read_text(encoding="utf-8").strip()
    return prompt.strip()


def normalized_language(language: str) -> str | None:
    value = language.strip().lower()
    return None if value in {"", "auto", "detect"} else value


def language_candidates(language: str) -> list[str]:
    normalized = normalized_language(language)
    if normalized:
        return [normalized]
    return ["ru", "uz"]


def transcript_score(text: str, language: str) -> int:
    lowered = text.lower()
    letters = sum(1 for ch in lowered if ch.isalpha())
    if not letters:
        return -1000
    cyrillic = sum(1 for ch in lowered if "а" <= ch <= "я" or ch in "ёўқғҳ")
    uz_markers = sum(lowered.count(marker) for marker in (
        "salom", "assalom", "alaykum", "bo", "bol", "qil", "kerak", "rahmat", "ha", "yoq", "bor", "sum", "so'm", "o'zbek",
    ))
    ru_markers = sum(lowered.count(marker) for marker in (
        "да", "нет", "нужно", "надо", "сум", "пров", "сдел", "буд", "что", "как", "когда",
    ))
    repeated_noise = len([word for word in lowered.split() if len(word) > 18])
    score = min(len(lowered), 300) - repeated_noise * 20
    if language == "ru":
        score += cyrillic * 2 + ru_markers * 30
        score -= uz_markers * 10
    elif language == "uz":
        score += uz_markers * 30
        score += sum(1 for ch in lowered if ch in "ўқғҳ") * 3
        score -= ru_markers * 10
    return score


def ensure_ffmpeg() -> str:
    current = shutil.which("ffmpeg")
    if current:
        return current
    candidates = [
        Path.home() / "bin" / "ffmpeg",
        Path("/opt/homebrew/bin/ffmpeg"),
        Path("/usr/local/bin/ffmpeg"),
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            os.environ["PATH"] = f"{candidate.parent}:{os.environ.get('PATH', '')}"
            return str(candidate)
    raise RuntimeError("ffmpeg is required to decode Telegram voice messages")


def prepare_audio(input_path: Path) -> Path:
    ffmpeg = ensure_ffmpeg()
    target = Path(tempfile.mkdtemp(prefix="tg-voice-prep-")) / "speech.wav"
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-af",
            "highpass=f=80,lowpass=f=7800,loudnorm=I=-18:TP=-1.5:LRA=11",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return target


def write_result(output_path: Path, text: str, language: str | None, engine: str, model_name: str) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    meta_path = output_path.with_suffix(output_path.suffix + ".meta.json")
    meta_path.write_text(
        json.dumps(
            {"language": language or "unknown", "engine": engine, "model": model_name},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def transcribe_mlx(input_path: Path, model_name: str, language: str, prompt: str = "") -> tuple[str, str | None]:
    try:
        import mlx_whisper
    except Exception as exc:  # noqa: BLE001 - fallback handles missing optional dependency.
        raise RuntimeError("mlx-whisper is not installed") from exc
    mlx_repos = {
        "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    }
    model_repo = model_name if "/" in model_name else mlx_repos.get(model_name, f"mlx-community/whisper-{model_name}-mlx")
    result = mlx_whisper.transcribe(
        str(input_path),
        path_or_hf_repo=model_repo,
        language=normalized_language(language),
        task="transcribe",
        initial_prompt=prompt or None,
    )
    text = str(result.get("text") or "").strip()
    if not text:
        raise RuntimeError("mlx-whisper returned empty transcript")
    return text, str(result.get("language") or normalized_language(language) or "unknown")


def transcribe_faster(input_path: Path, model_name: str, language: str, prompt: str = "") -> tuple[str, str | None]:
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # noqa: BLE001 - CLI should explain missing local dependency.
        raise RuntimeError(
            "faster-whisper is not installed. Run scripts/install_local_voice_stack.sh first."
        ) from exc

    model = WhisperModel(model_name, device="auto", compute_type="auto")
    segments, info = model.transcribe(
        str(input_path),
        language=normalized_language(language),
        vad_filter=True,
        initial_prompt=prompt or None,
    )
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    return text, str(getattr(info, "language", None) or normalized_language(language) or "unknown")


def transcribe(input_path: Path, output_path: Path, model_name: str, language: str, engine: str, prompt: str = "") -> str:
    prepared_path = prepare_audio(input_path)
    errors: list[str] = []
    engines = [engine] if engine != "auto" else ["mlx", "faster"]
    best: tuple[int, str, str | None, str, str] | None = None
    for candidate in engines:
        for candidate_language in language_candidates(language):
            try:
                text, detected_language = transcribe_mlx(prepared_path, model_name, candidate_language, prompt) if candidate == "mlx" else transcribe_faster(prepared_path, model_name, candidate_language, prompt)
                score = transcript_score(text, candidate_language)
                if best is None or score > best[0]:
                    best = (score, text, detected_language, candidate, candidate_language)
                if normalized_language(language):
                    write_result(output_path, text, detected_language, candidate, model_name)
                    return text
            except Exception as exc:  # noqa: BLE001 - report all attempted local engines.
                errors.append(f"{candidate}/{candidate_language}: {exc}")
    if best:
        _, text, detected_language, candidate, candidate_language = best
        write_result(output_path, text, detected_language or candidate_language, candidate, model_name)
        return text
    raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="auto", help="Language code such as ru/uz, or auto for detection")
    parser.add_argument("--engine", choices=["auto", "mlx", "faster"], default="auto")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--prompt-file", type=Path)
    args = parser.parse_args()
    prompt = read_prompt(args.prompt, args.prompt_file)
    print(transcribe(args.input, args.output, args.model, args.language, args.engine, prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
