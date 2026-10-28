"""Turn the open Avtocod card into a short reply. Sample blocks are not included."""

from __future__ import annotations

import re


def parse(raw: str) -> tuple[str, str]:
    title, _, body = raw.partition("\n---\n")
    name = ""
    found = re.search(r"Проверить\s+(.+?)\s+по госномеру", title)
    if found:
        name = found.group(1).strip(" ,.—")
    vin = _grab(body, r"VIN[:\s|]*([A-HJ-NPR-Z0-9*]{4,})")
    year = _grab(body, r"Год производства:?\s*(\d{4})")
    fuel = _grab(body, r"(?:Тип двигателя|Двигатель):?\s*([A-Za-zА-Яа-яЁё\-]+)")
    category = _grab(body, r"Категория(?:\s+ТС)?:?\s*([A-ZА-Я0-9]+)")
    power = _grab(body, r"Мощность(?:\s+двигателя)?:?\s*(\d[\d\s.,]*л\.?\s*с\.?)")
    wheel = _grab(body, r"(?:Расположение руля|Руль):?\s*([A-Za-zА-Яа-яЁё]+)")
    volume = _grab(body, r"Объ[её]м(?:\s+двигателя)?:?\s*(\d[\d\s]*куб[^\n]*)")
    mass = _grab(body, r"Масса(?:\s+авто)?:?\s*(\d[\d\s]*кг)")
    lines: list[str] = []
    if name and year:
        lines.append(f"{name}, {year}")
    elif name:
        lines.append(name)
    elif year:
        lines.append(year)
    if vin:
        lines.append(f"VIN {vin}")
    engine = [part for part in (fuel.lower() if fuel else "", _volume(volume), _power(power)) if part]
    if engine:
        lines.append(", ".join(engine))
    rest = []
    if wheel:
        rest.append(f"руль {wheel.lower()}")
    if category:
        rest.append(f"категория {category}")
    if mass:
        rest.append(mass.strip())
    if rest:
        lines.append(", ".join(rest))
    if not lines:
        lines.append(title.strip() or "Карточка без характеристик")
    return (name or lines[0]), "\n".join(lines)


def _grab(text: str, pattern: str) -> str:
    found = re.search(pattern, text, re.IGNORECASE)
    if not found:
        return ""
    for group in found.groups():
        if group and group.strip(" :|-"):
            return re.sub(r"\s+", " ", group).strip(" |")
    return ""


def useful(body: str) -> bool:
    return re.search(r"\b(?:19|20)\d{2}\b", body) is not None


def _power(value: str) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", "", value)
    return re.sub(r"^(\d+)", r"\1 ", compact)


def _volume(value: str) -> str:
    if not value:
        return ""
    return value.replace("куб.см", "см³").replace("куб. см", "см³").replace("куб см", "см³")
