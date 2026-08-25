"""Validated shortcut specifications shared by menus and native registration."""

from __future__ import annotations

from dataclasses import dataclass


_KEY_CODES = {
    "A": 0x00,
    "B": 0x0B,
    "C": 0x08,
    "D": 0x02,
    "E": 0x0E,
    "F": 0x03,
    "G": 0x05,
    "H": 0x04,
    "I": 0x22,
    "J": 0x26,
    "K": 0x28,
    "L": 0x25,
    "M": 0x2E,
    "N": 0x2D,
    "O": 0x1F,
    "P": 0x23,
    "Q": 0x0C,
    "R": 0x0F,
    "S": 0x01,
    "T": 0x11,
    "U": 0x20,
    "V": 0x09,
    "W": 0x0D,
    "X": 0x07,
    "Y": 0x10,
    "Z": 0x06,
    "0": 0x1D,
    "1": 0x12,
    "2": 0x13,
    "3": 0x14,
    "4": 0x15,
    "5": 0x17,
    "6": 0x16,
    "7": 0x1A,
    "8": 0x1C,
    "9": 0x19,
}

_MODIFIERS = {
    "command": ("Command", "⌘", 1 << 20, 1 << 8),
    "control": ("Control", "⌃", 1 << 18, 1 << 12),
    "option": ("Option", "⌥", 1 << 19, 1 << 11),
    "shift": ("Shift", "⇧", 1 << 17, 1 << 9),
}
_MODIFIER_ORDER = ("command", "control", "option", "shift")


@dataclass(frozen=True)
class ShortcutSpec:
    canonical: str
    display: str
    key_equivalent: str
    appkit_modifiers: int
    carbon_key_code: int
    carbon_modifiers: int


def parse_shortcut(value: str) -> ShortcutSpec:
    """Parse modified ANSI letter/digit shortcuts such as Option-Shift-T."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("shortcut must be a non-empty string")
    parts = [part.strip() for part in value.split("-")]
    if any(not part for part in parts) or len(parts) < 2:
        raise ValueError("shortcut must contain a modifier and one key")
    key = parts[-1].upper()
    if key not in _KEY_CODES:
        raise ValueError("shortcut key must be one ANSI letter or digit")
    modifier_names = [part.casefold() for part in parts[:-1]]
    if len(set(modifier_names)) != len(modifier_names):
        raise ValueError("shortcut modifiers must not be repeated")
    if any(name not in _MODIFIERS for name in modifier_names):
        raise ValueError("shortcut contains an unsupported modifier")
    selected = [name for name in _MODIFIER_ORDER if name in modifier_names]
    if not selected:
        raise ValueError("shortcut must contain at least one modifier")
    canonical_modifiers = [_MODIFIERS[name][0] for name in selected]
    display_modifiers = [_MODIFIERS[name][1] for name in selected]
    appkit_modifiers = sum(_MODIFIERS[name][2] for name in selected)
    carbon_modifiers = sum(_MODIFIERS[name][3] for name in selected)
    return ShortcutSpec(
        canonical="-".join([*canonical_modifiers, key]),
        display="".join([*display_modifiers, key]),
        key_equivalent=key.casefold(),
        appkit_modifiers=appkit_modifiers,
        carbon_key_code=_KEY_CODES[key],
        carbon_modifiers=carbon_modifiers,
    )


__all__ = ["ShortcutSpec", "parse_shortcut"]
