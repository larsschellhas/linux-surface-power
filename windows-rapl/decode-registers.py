#!/usr/bin/env python3
"""Decode raw RAPL register values typed in from a Windows register tool (or from Linux). Read-only, runs anywhere with Python 3.
Units are read from MSR 0x606 if given, else the values seen on this machine (power 1/8 W, time 1/1024 s).
Usage example (values from the Linux boot dump):
  python3 decode-registers.py --u606 a0e03 --m610 42820000dd8118 --mmio59a0 0000811800DD80E0 --m65c 824000dd8180 --m601 2b0 --m615 a4882f8 --m1a2 6e00b1
Any subset can be given. Hex, with or without 0x."""
import argparse

def h(s):
    return int(s, 16)

def tw(v):  # time window field, 7 bits: Y in bits 4:0, Z in bits 6:5
    y, z = v & 0x1F, (v >> 5) & 3
    return y, z

ap = argparse.ArgumentParser()
for n in ('u606', 'm610', 'mmio59a0', 'm65c', 'm601', 'm615', 'm1a2', 'mmio5b10'):
    ap.add_argument('--' + n)
a = ap.parse_args()

pu, tu = 1 / 8, 1 / 1024
if a.u606:
    u = h(a.u606)
    pu, eu, tu = 1 / (1 << (u & 0xF)), 1 / (1 << ((u >> 8) & 0x1F)), 1 / (1 << ((u >> 16) & 0xF))
    print(f'0x606 units: power 1/{1 / pu:g} W, energy 2^-{(u >> 8) & 0x1F} J, time 1/{1 / tu:g} s')

def twin(v):
    y, z = tw(v)
    return (1 << y) * (1 + z / 4) * tu

def limit(name, v):
    pl1 = (v & 0x7FFF) * pu
    pl2 = ((v >> 32) & 0x7FFF) * pu
    print(f'{name}: PL1 {pl1:g} W (enable {(v >> 15) & 1}, clamp {(v >> 16) & 1}, window {twin((v >> 17) & 0x7F):g} s)  '
          f'PL2 {pl2:g} W (enable {(v >> 47) & 1}, clamp {(v >> 48) & 1}, window {twin((v >> 49) & 0x7F):g} s)  lock {(v >> 63) & 1}')

if a.m610: limit('MSR 0x610 package limit      ', h(a.m610))
if a.mmio59a0: limit('MMIO MCHBAR+0x59A0 package   ', h(a.mmio59a0))
if a.m65c:
    v = h(a.m65c)
    print(f'MSR 0x65C psys: PL1 {(v & 0x7FFF) * pu:g} W, PL2 {((v >> 32) & 0x7FFF) * pu:g} W (enable bits {(v >> 15) & 1}/{(v >> 47) & 1})')
if a.m601: print(f'MSR 0x601 PL4: {(h(a.m601) & 0x1FFF) * pu:g} W')
if a.m615: print(f'MSR 0x615 PL3: {(h(a.m615) & 0x7FFF) * pu:g} W')
if a.m1a2:
    v = h(a.m1a2)
    print(f'MSR 0x1A2: TjMax {(v >> 16) & 0xFF} C, TCC offset {(v >> 24) & 0x3F} C')
if a.mmio5b10: print(f'MMIO MCHBAR+0x5B10: {h(a.mmio5b10):#x} (unidentified)')
