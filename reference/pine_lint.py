#!/usr/bin/env python3
"""
Механическая проверка .pine-файлов по чек-листу известных ловушек Pine v6.
Не заменяет компилятор TradingView: ловит только то, что видно по тексту.

  python reference/pine_lint.py pine/*.pine

Проверки:
  1. //@version=6, indicator(), нет strategy()/study()/security() без namespace.
  2. input.time/int/float: defval — числовой литерал (const), не timestamp().
  3. ta.* — только на верхнем уровне (отступ 0) или на верхнем уровне тела функции
     (отступ 4), не в if/for/while, не после ?, and, or в той же строке.
     Функции, внутри которых есть ta.*, вызываются тоже только безусловно.
  4. request.security: не больше 3 вызовов, всегда lookahead = barmerge.lookahead_off.
  5. Заданы max_lines_count, max_labels_count, max_boxes_count (если объекты создаются).
  6. table.new — только в объявлении var; таблица заполняется в if barstate.islast.
  7. Нет эмодзи.
"""
from __future__ import annotations

import re
import sys

TA_RE = re.compile(r"\bta\.\w+\s*\(")
FUNC_DEF_RE = re.compile(r"^(\w+)\s*\(([^)]*)\)\s*=>")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")


def strip_strings_comments(line: str) -> str:
    out, q = [], None
    i = 0
    while i < len(line):
        ch = line[i]
        if q:
            if ch == "\\":
                i += 2
                continue
            if ch == q:
                q = None
            out.append(" ")
        elif ch in "\"'":
            q = ch
            out.append(" ")
        elif line.startswith("//", i):
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def lint(path: str) -> list[str]:
    raw = open(path, encoding="utf-8").read().splitlines()
    code = [strip_strings_comments(l) for l in raw]
    errs: list[str] = []

    def err(n, msg):
        errs.append(f"{path}:{n + 1}: {msg}")

    if not raw or raw[0].strip() != "//@version=6":
        err(0, "первая строка должна быть //@version=6")
    text = "\n".join(code)
    if not re.search(r"^indicator\(", text, re.M):
        err(0, "нет indicator()")
    for bad in (r"\bstrategy\s*\(", r"\bstudy\s*\(", r"(?<!request\.)\bsecurity\s*\(",
                r"\btransp\s*=", r"\bwhen\s*=", r"lookahead_on"):
        for n, l in enumerate(code):
            if re.search(bad, l):
                err(n, f"запрещено: {bad}")

    # 2. defval у input.time/int/float
    for n, l in enumerate(code):
        m = re.search(r"\binput\.(time|int|float)\(\s*([^,)]+)", raw[n])
        if m:
            dv = m.group(2).strip()
            if not re.fullmatch(r"-?\d+(\.\d+)?", dv):
                err(n, f"defval input.{m.group(1)} должен быть числовым литералом: {dv}")

    # функции и их тела
    funcs, body_of = {}, {}
    cur = None
    for n, l in enumerate(code):
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip(" "))
        m = FUNC_DEF_RE.match(l)
        if ind == 0 and m:
            cur = m.group(1)
            funcs[cur] = n
            continue
        if ind == 0:
            cur = None
        elif cur:
            body_of[n] = cur

    # функции, содержащие ta.* (прямо или через другие такие функции)
    ta_funcs = {f for n, f in body_of.items() if TA_RE.search(code[n])}
    changed = True
    while changed:
        changed = False
        for n, f in body_of.items():
            if f not in ta_funcs and any(re.search(rf"\b{g}\s*\(", code[n]) for g in ta_funcs):
                ta_funcs.add(f)
                changed = True

    call_res = [TA_RE] + [re.compile(rf"\b{f}\s*\(") for f in ta_funcs]
    for n, l in enumerate(code):
        if not l.strip() or FUNC_DEF_RE.match(l):
            continue
        ind = len(l) - len(l.lstrip(" "))
        # продолжение строки (отступ не кратен 4) — относим к предыдущей логической строке
        if ind % 4:
            continue
        allowed = 4 if n in body_of else 0
        for cre in call_res:
            for m in cre.finditer(l):
                before = l[:m.start()]
                # вызов внутри request.security(...) — допустим (вычисляется в контексте ТФ)
                if "request.security(" in before:
                    continue
                if ind > allowed:
                    err(n, f"вызов {m.group(0).strip()} во вложенном блоке (отступ {ind})")
                elif re.search(r"\?|\band\b|\bor\b", before):
                    err(n, f"вызов {m.group(0).strip()} после ?, and или or")

    # 4. request.security
    sec = [n for n, l in enumerate(code) if "request.security(" in l]
    if len(sec) > 3:
        err(sec[3], f"request.security вызывается {len(sec)} раз (> 3)")
    for n in sec:
        if "lookahead = barmerge.lookahead_off" not in code[n]:
            err(n, "request.security без lookahead = barmerge.lookahead_off")

    # 5. лимиты объектов
    head = " ".join(code[:15])
    for kind, lim in (("line.new", "max_lines_count"), ("label.new", "max_labels_count"), ("box.new", "max_boxes_count")):
        if kind in text and lim not in head:
            err(0, f"используется {kind}, но не задан {lim}")

    # 6. таблицы
    for n, l in enumerate(code):
        if "table.new(" in l and not re.match(r"^var\s+table\s+\w+\s*=", l):
            err(n, "table.new должен быть в объявлении var table")
        if re.search(r"\btable\.(cell|merge_cells|clear)\(", l) and n not in body_of:
            # должен быть внутри блока if barstate.islast (ищем ближайший блок верхнего уровня выше)
            k = n
            while k > 0 and (not code[k].strip() or code[k].startswith(" ")):
                k -= 1
            if "barstate.islast" not in code[k]:
                err(n, "заполнение таблицы вне if barstate.islast")

    # 7. эмодзи
    for n, l in enumerate(raw):
        if EMOJI_RE.search(l):
            err(n, "эмодзи в коде")
    return errs


def main(argv):
    errs = [e for p in argv for e in lint(p)]
    for e in errs:
        print(e)
    print(f"Проверено файлов: {len(argv)}, замечаний: {len(errs)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
