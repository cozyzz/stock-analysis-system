# -*- coding: utf-8 -*-
s = open('_logic.js', encoding='utf-8').read()
print('含反引号:', s.count('`'))
i = 0; n = len(s); stack = []; errs = []
while i < n:
    c = s[i]
    if c == '/' and i + 1 < n and s[i + 1] == '/':
        while i < n and s[i] != '\n':
            i += 1
        continue
    if c == '/' and i + 1 < n and s[i + 1] == '*':
        i += 2
        while i < n - 1 and not (s[i] == '*' and s[i + 1] == '/'):
            i += 1
        i += 2
        continue
    if c in '"\'':
        q = c; i += 1
        while i < n and s[i] != q:
            if s[i] == '\\':
                i += 2
            else:
                i += 1
        if i >= n:
            errs.append(('unclosed_str', q, i)); break
        i += 1; continue
    if c == '`':
        i += 1
        while i < n and s[i] != '`':
            if s[i] == '\\':
                i += 2
            else:
                i += 1
        if i >= n:
            errs.append(('unclosed_tpl', i)); break
        i += 1; continue
    if c in '([{':
        stack.append((c, i))
    elif c in ')]}':
        if not stack:
            errs.append(('extra', c, i)); break
        oc, oi = stack.pop()
        exp = {'(': ')', '[': ']', '{': '}'}[oc]
        if c != exp:
            errs.append(('mismatch', oc, oi, c, i)); break
    i += 1


def ln(p):
    return s[:p].count('\n') + 1


if errs:
    e = errs[0]
    p = e[-1]
    print('ERR:', e[0], 'line', ln(p), 'ctx:', repr(s[max(0, p - 50):p + 15]))
elif stack:
    oc, oi = stack[-1]
    print('UNCLOSED:', oc, 'line', ln(oi), 'ctx:', repr(s[max(0, oi - 60):oi + 25]))
    print('栈深度', len(stack))
    for c, p in stack[:5]:
        print('  open:', c, 'line', ln(p), repr(s[max(0, p - 40):p + 15]))
else:
    print('BALANCED')
