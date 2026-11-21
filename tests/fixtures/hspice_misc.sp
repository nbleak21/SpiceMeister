* HSPICE fixture (no subckt)
.param r0=1k g0='r0*2'
.global vdd
R1 a b r='pow(r0,1)*2'
G1 out 0 CUR='V(a)*1m'
.model nm nmos level=49 vto=0.7 $ bsim3 model
.option scale=1u
.lib 'corners.lib' TT
