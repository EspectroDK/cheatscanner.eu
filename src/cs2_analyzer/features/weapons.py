"""Weapon name normalization.

Demo sources disagree on naming: tick props give display names ("AK-47"),
``weapon_fire`` gives entity class names ("weapon_ak47") and damage events give
short names ("ak47"). Everything is mapped to a canonical short id and a class
so baselines can be stratified per weapon / weapon class.
"""

from __future__ import annotations

import re

_CANON: dict[str, tuple[str, str]] = {
    # canonical id: (class, display)
    "ak47": ("rifle", "AK-47"),
    "m4a1": ("rifle", "M4A4"),
    "m4a1_silencer": ("rifle", "M4A1-S"),
    "famas": ("rifle", "FAMAS"),
    "galilar": ("rifle", "Galil AR"),
    "aug": ("rifle", "AUG"),
    "sg556": ("rifle", "SG 553"),
    "awp": ("sniper", "AWP"),
    "ssg08": ("sniper", "SSG 08"),
    "scar20": ("sniper", "SCAR-20"),
    "g3sg1": ("sniper", "G3SG1"),
    "mac10": ("smg", "MAC-10"),
    "mp9": ("smg", "MP9"),
    "mp7": ("smg", "MP7"),
    "mp5sd": ("smg", "MP5-SD"),
    "ump45": ("smg", "UMP-45"),
    "p90": ("smg", "P90"),
    "bizon": ("smg", "PP-Bizon"),
    "nova": ("shotgun", "Nova"),
    "xm1014": ("shotgun", "XM1014"),
    "sawedoff": ("shotgun", "Sawed-Off"),
    "mag7": ("shotgun", "MAG-7"),
    "m249": ("mg", "M249"),
    "negev": ("mg", "Negev"),
    "glock": ("pistol", "Glock-18"),
    "hkp2000": ("pistol", "P2000"),
    "usp_silencer": ("pistol", "USP-S"),
    "p250": ("pistol", "P250"),
    "elite": ("pistol", "Dual Berettas"),
    "fiveseven": ("pistol", "Five-SeveN"),
    "tec9": ("pistol", "Tec-9"),
    "cz75a": ("pistol", "CZ75-Auto"),
    "deagle": ("pistol", "Desert Eagle"),
    "revolver": ("pistol", "R8 Revolver"),
    "taser": ("taser", "Zeus x27"),
    "knife": ("knife", "Knife"),
    "hegrenade": ("grenade", "High Explosive Grenade"),
    "flashbang": ("grenade", "Flashbang"),
    "smokegrenade": ("grenade", "Smoke Grenade"),
    "molotov": ("grenade", "Molotov"),
    "incgrenade": ("grenade", "Incendiary Grenade"),
    "decoy": ("grenade", "Decoy Grenade"),
    "c4": ("c4", "C4 Explosive"),
    "inferno": ("grenade", "Fire"),
    "world": ("other", "World"),
}

_BY_DISPLAY = {v[1].lower(): k for k, v in _CANON.items()}
_BY_DISPLAY.update({"m4a1-s": "m4a1_silencer", "usp-s": "usp_silencer", "zeus x27": "taser"})

GUN_CLASSES = {"rifle", "sniper", "smg", "shotgun", "mg", "pistol"}


def canonical_weapon(name: str | None) -> str | None:
    if name is None or (isinstance(name, float)):
        return None
    raw = str(name).strip()
    if not raw:
        return None
    low = raw.lower()
    if low in _BY_DISPLAY:
        return _BY_DISPLAY[low]
    low = low.removeprefix("weapon_")
    if low in _CANON:
        return low
    if "knife" in low or "bayonet" in low or "karambit" in low or "dagger" in low:
        return "knife"
    return re.sub(r"[^a-z0-9_]", "", low) or None


def weapon_class(name: str | None) -> str:
    canon = canonical_weapon(name)
    if canon is None:
        return "unknown"
    return _CANON.get(canon, ("unknown", ""))[0]


def is_gun(name: str | None) -> bool:
    return weapon_class(name) in GUN_CLASSES
