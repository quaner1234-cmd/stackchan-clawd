"""Bounded local USB control protocol; angles are tenths of degrees on the wire."""
import binascii
import math
import struct


def bounded(value, low, high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('请输入有效数字。')
    return max(low,min(high,int(round(value))))


def packet(kind, values=()):
    if kind=='status':data=struct.pack('<BhhB',0,0,0,0)
    elif kind=='pose':
        yaw,pitch=values
        data=struct.pack('<BhhB',1,bounded(yaw,-300,300),bounded(pitch,30,450),0)
    elif kind=='volume':data=struct.pack('<BhhB',2,0,0,bounded(values[0],0,100))
    else:raise ValueError('Unknown control')
    return b'G'+data+struct.pack('<I',binascii.crc32(data))
