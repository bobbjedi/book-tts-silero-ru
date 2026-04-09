def chunk_text_for_vosk(*args, **kwargs):
    from .chunker import chunk_text_for_vosk as _impl

    return _impl(*args, **kwargs)


def extract_fb2_chapters(*args, **kwargs):
    from .fb2_synthesize import extract_fb2_chapters as _impl

    return _impl(*args, **kwargs)


def synthesize_fb2_to_mp3_chapters(*args, **kwargs):
    from .fb2_synthesize import synthesize_fb2_to_mp3_chapters as _impl

    return _impl(*args, **kwargs)


def synthesize_txt_to_wav(*args, **kwargs):
    from .synthesize import synthesize_txt_to_wav as _impl

    return _impl(*args, **kwargs)

__all__ = [
    "chunk_text_for_vosk",
    "extract_fb2_chapters",
    "synthesize_fb2_to_mp3_chapters",
    "synthesize_txt_to_wav",
]
