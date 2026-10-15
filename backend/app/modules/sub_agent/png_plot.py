"""Dependency-free PNG line charts, copied into the read-only WASI package.

No host execution, network, font files or third-party plotting dependency.
Labels use a small ASCII bitmap font; provide translated captions in the reply.
"""
import struct
import math

FONT = dict(zip('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-./ :', [
 '111101101101111','010110010010111','111001111100111','111001111001111','101101111001001',
 '111100111001111','111100111101111','111001010010010','111101111101111','111101111001111',
 '010101111101101','110101110101110','011100100100011','110101101101110','111100110100111',
 '111100110100100','011100101101011','101101111101101','111010010010111','001001001101010',
 '101101110101101','100100100100111','101111111101101','101111111111101','010101101101010',
 '110101110100100','010101101111011','110101110101101','011100010001110','111010010010010',
 '101101101101111','101101101101010','101101111111101','101101010101101','101101010010010',
 '111001010100111','000000111000000','000000000000010','001001010100100','000000000000000','000010000010000']))


def crc32(data):
    table=[]
    for i in range(256):
        c=i
        for _ in range(8): c=(c>>1) ^ (0xedb88320 if c&1 else 0)
        table.append(c)
    c=0xffffffff
    for b in data: c=table[(c^b)&255] ^ (c>>8)
    return c ^ 0xffffffff


def chunk(kind, data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',crc32(kind+data))


def _axis_scale(values):
    low, high = min(values), max(values)
    span = high - low
    padding = span * .1 if span else abs(low) * .01 or .01
    lo, hi = low - padding, high + padding
    step = (hi - lo) / 4
    if not all(math.isfinite(v) for v in (lo, hi, step)) or step <= 0:
        raise ValueError('Chart range cannot be represented with finite distinct ticks')
    magnitude = max(abs(lo), abs(hi))
    # Keep rounding error well below the spacing of the actual grid lines.
    precision = max(1, min(17, math.floor(math.log10(magnitude)) - math.floor(math.log10(step)) + 3))
    ticks = []
    for j in range(5):
        value = lo + step * j
        if value == 0 or abs(value) < step * 1e-10:
            value = 0.0
        label = format(value, '.' + str(precision) + 'g').upper().replace('E+', 'E')
        ticks.append(label)
    if len(set(ticks)) != len(ticks):
        raise ValueError('Chart range is too narrow for distinct tick labels')
    return lo, hi, ticks


def line_chart(labels, values, title='SAMPLE', unit='VALUE'):
    """Return RGB PNG bytes for 2..1000 finite points. Use ASCII labels."""
    values=[float(v) for v in values]
    if len(values)!=len(labels) or not 2<=len(values)<=1000 or any(not math.isfinite(v) for v in values):
        raise ValueError('Chart requires 2..1000 finite points and matching labels')
    w,h=640,320
    pixels=bytearray([255])*(w*h*3)
    def dot(x,y,color):
        if 0<=x<w and 0<=y<h: pixels[(y*w+x)*3:(y*w+x)*3+3]=bytes(color)
    def line(x0,y0,x1,y1,color):
        n=max(abs(x1-x0),abs(y1-y0),1)
        for k in range(n+1): dot(round(x0+(x1-x0)*k/n),round(y0+(y1-y0)*k/n),color)
    def text(x,y,s):
        for c in str(s).upper()[:70]:
            glyph=FONT.get(c,FONT[' '])
            for yy in range(5):
                for xx in range(3):
                    if glyph[yy*3+xx]=='1':
                        for dy in range(2):
                            for dx in range(2): dot(x+xx*2+dx,y+yy*2+dy,(40,40,40))
            x+=8
    lo,hi,ticks=_axis_scale(values)
    axis_x=max(60, max(len(label) for label in ticks)*8+8)
    plot_left=axis_x+10
    points=[(plot_left+round(i*(590-plot_left)/(len(values)-1)),250-round((v-lo)/(hi-lo)*195)) for i,v in enumerate(values)]
    text(axis_x+5,15,title);text(5,36,unit)
    line(axis_x,50,axis_x,255,(90,90,90));line(axis_x,255,610,255,(90,90,90))
    for j in range(5):
        y=250-round(j*195/4)
        text(2,y-4,ticks[j]);line(axis_x+1,y,610,y,(225,230,235))
    selected={round(i*(len(values)-1)/4) for i in range(5)}
    for i,(x,y) in enumerate(points):
        if i: line(*points[i-1],x,y,(40,95,180))
        if i in selected:text(x-30,275,str(labels[i])[-5:])
    text(285,300,'DATE')
    scan=b''.join(b'\0'+pixels[y*w*3:(y+1)*w*3] for y in range(h))
    # zlib wrapper containing stored DEFLATE blocks, no zlib extension needed.
    compressed=bytearray(b'\x78\x01')
    for offset in range(0,len(scan),65535):
        block=scan[offset:offset+65535]
        compressed+=bytes([1 if offset+len(block)==len(scan) else 0])+struct.pack('<HH',len(block),65535-len(block))+block
    a,b=1,0
    for byte in scan:a=(a+byte)%65521;b=(b+a)%65521
    compressed+=struct.pack('>I',(b<<16)|a)
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,2,0,0,0))
        +chunk(b'tEXt',('Title\0'+title).encode('latin1',errors='replace'))+chunk(b'IDAT',bytes(compressed))+chunk(b'IEND',b''))
