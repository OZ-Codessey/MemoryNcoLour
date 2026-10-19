"""MaC 색 엔진 (KS A 0011 기반). 사용: from services.mac_color import analyze_sentence, NoColors, SplitFailed"""
from .palette import NoColors
from .pipeline import JsonFileStore, MemoryStore, SplitFailed, analyze_sentence

__all__ = ["analyze_sentence", "NoColors", "SplitFailed", "MemoryStore", "JsonFileStore"]
