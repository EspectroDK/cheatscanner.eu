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

# Base damage per bullet at point blank, before armour, hit group and range fall-off (public weapon data).
# Shotguns fire several pellets; their value is one full load.
BASE_DAMAGE: dict[str, float] = {
    "ak47": 36, "m4a1": 33, "m4a1_silencer": 38, "famas": 30, "galilar": 30, "aug": 28, "sg556": 30,
    "awp": 115, "ssg08": 88, "scar20": 80, "g3sg1": 80,
    "mac10": 29, "mp9": 26, "mp7": 29, "mp5sd": 27, "ump45": 35, "p90": 26, "bizon": 27,
    "nova": 26 * 9, "xm1014": 20 * 6, "sawedoff": 32 * 8, "mag7": 30 * 8,
    "m249": 32, "negev": 35,
    "glock": 30, "hkp2000": 35, "usp_silencer": 35, "p250": 38, "elite": 38, "fiveseven": 32, "tec9": 33,
    "cz75a": 31, "deagle": 53, "revolver": 86,
}
HITGROUP_MULTIPLIER = {"head": 4.0, "chest": 1.0, "stomach": 1.25, "left_arm": 1.0, "right_arm": 1.0,
                       "left_leg": 0.75, "right_leg": 0.75, "neck": 4.0, "gear": 1.0, "generic": 1.0}


def max_unpenetrated_damage(name: str | None, hitgroup: str | None) -> float | None:
    """Most damage one bullet of this weapon can do to that hit group without passing through anything.

    None when the weapon or its damage is unknown."""
    base = BASE_DAMAGE.get(canonical_weapon(name) or "")
    if base is None:
        return None
    return base * HITGROUP_MULTIPLIER.get(str(hitgroup or "generic").lower(), 1.0)


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
