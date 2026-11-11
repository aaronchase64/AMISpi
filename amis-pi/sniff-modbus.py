import socket,struct,time
s=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0800));s.settimeout(1)
end=time.monotonic()+50
print('Capture Modbus TCP/502 for 50 seconds',flush=True)
while time.monotonic()<end:
 try:p,addr=s.recvfrom(65535)
 except socket.timeout:continue
 if len(p)<54 or p[23]!=6:continue
 off=14+(p[14]&15)*4
 src,dst=struct.unpack('>HH',p[off:off+4])
 if 502 not in (src,dst):continue
 ip1=socket.inet_ntoa(p[26:30]);ip2=socket.inet_ntoa(p[30:34])
 data=p[off+(p[off+12]>>4)*4:]
 print(addr[0],ip1,src,'->',ip2,dst,'flags',hex(p[off+13]),'modbus',data[:64].hex(),flush=True)
