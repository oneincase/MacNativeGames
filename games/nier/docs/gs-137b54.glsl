#version 460
#extension GL_NV_geometry_shader_passthrough : require
#extension GL_KHR_shader_subgroup_basic : require
#extension GL_KHR_shader_subgroup_shuffle : require
#extension GL_KHR_shader_subgroup_ballot : require
layout(triangles) in;

layout(passthrough) in gl_PerVertex
{
    vec4 gl_Position;
    float gl_PointSize;
    float gl_ClipDistance[8];
    float gl_CullDistance[1];
} gl_in[];

layout(set = 0, binding = 0, std140) uniform support_buffer
{
    uint _m0;
    uint _m1[8];
    vec4 _m2;
    vec4 _m3;
    int _m4;
    float _m5[73];
    ivec4 _m6;
    int _m7;
} support_buffer_1;

layout(set = 0, binding = 1, std430) buffer gp_s0
{
    uint _m0[];
} gp_s0_1;

layout(set = 0, binding = 2, std430) buffer gp_s1
{
    uint _m0[];
} gp_s1_1;

layout(passthrough, location = 4) in vec4 _25[3];
layout(passthrough, location = 2) in vec4 _27[3];
layout(passthrough, location = 3) in vec4 _37[3];

int _54(int _549, int _550, int _551, out int _552)
{
    float _555 = uintBitsToFloat(gl_SubgroupInvocationID);
    int _557 = floatBitsToInt(_555) - _550;
    bool _559 = _557 >= (floatBitsToInt(_555) & (int(uint(_551) >> uint(8)) & 31));
    _552 = _559 ? (-1) : 0;
    return _559 ? floatBitsToInt(subgroupShuffle(intBitsToFloat(_549), uint(_557))) : _549;
}

void main()
{
    int _57 = 0;
    int _59 = 1;
    int _61 = 2;
    int _63 = 1;
    if (false)
    {
        _57 = int(0x80000000);
    }
    int _65 = _57;
    if (false)
    {
        _59 = int(0x80000000);
    }
    int _67 = _59;
    float _70 = _25[_65].x;
    if (false)
    {
        _61 = int(0x80000000);
    }
    int _72 = _61;
    float _74 = _25[_67].x;
    float _76 = _25[_72].x;
    bool _80 = (floatBitsToInt(_76) & (floatBitsToInt(_70) & floatBitsToInt(_74))) != 0;
    int _82 = 0;
    int _84 = 0;
    if (!_80)
    {
        bool _86 = ((floatBitsToInt(_76) | (floatBitsToInt(_70) | floatBitsToInt(_74))) & 64) == 64;
        if (_86)
        {
            _82 = 1;
        }
        _84 = _82;
        if (!_86)
        {
            float _88 = _27[_72].x;
            float _90 = _27[_65].x;
            float _92 = _27[_65].y;
            float _94 = _27[_67].y;
            float _96 = _27[_72].y;
            float _98 = _27[_67].x;
            float _100 = (_88 + (-_90)) * ((-_92) + _94);
            bool _102 = ((_96 + (-_92)) * ((-_90) + _98)) >= _100;
            int _104 = floatBitsToInt(_100);
            if (_102)
            {
                _104 = 0;
            }
            int _106 = _104;
            if (!_102)
            {
                float _108 = max(_92, max(_96, _94)) + 0.5;
                float _110 = min(_92, min(_96, _94)) + 0.5;
                float _112 = max(_90, max(_88, _98)) + 0.5;
                float _114 = min(_90, min(_88, _98)) + 0.5;
                bool _116 = (((_108 != floor(_108)) || isnan(_108)) || isnan(floor(_108))) && ((((_110 != floor(_110)) || isnan(_110)) || isnan(floor(_110))) && ((((_112 != floor(_112)) || isnan(_112)) || isnan(floor(_112))) && ((((_114 != floor(_114)) || isnan(_114)) || isnan(floor(_114))) && ((floor(_114) == floor(_112)) || (floor(_110) == floor(_108))))));
                int _118 = floatBitsToInt(floor(_112));
                if (_116)
                {
                    _118 = 0;
                }
                int _120 = _118;
                if (!_116)
                {
                    _120 = 1;
                }
                _106 = _120;
            }
            _84 = _106;
        }
    }
    int _122 = _84;
    bool _124 = _122 == 1;
    bool _126 = int(subgroupBallot(_124).x) == 0;
    if (_126)
    {
        _63 = 0;
    }
    if (_126)
    {
        gl_BuiltIn_5253[0] = 0;
    }
    if (!(_63 != 0))
    {
        return;
    }
    int _128 = _122 * 3;
    int _130 = 1;
    int _132 = 0;
    int _134;
    int _441 = _54(_128, _130, _132, _134);
    int _136 = _441;
    int _138 = _136;
    if (int(gl_SubgroupInvocationID) == 0)
    {
        uint _457 = atomicAdd(gp_s0_1._m0[0], uint(bitCount(int(subgroupBallot(_124).x)) * 3));
        uint _141 = _457;
        _138 = int(_141);
    }
    int _143 = _138;
    int _145 = _143;
    int _147 = 1;
    int _149 = 0;
    int _151;
    int _463 = _54(_145, _147, _149, _151);
    int _153 = _463;
    int _155 = _143;
    if (_151 != 0)
    {
        _155 = _153 + _143;
    }
    int _157 = _155;
    int _159 = _157;
    int _161 = 2;
    int _163 = 0;
    int _165;
    int _474 = _54(_159, _161, _163, _165);
    int _167 = _474;
    int _169 = _157;
    if (_165 != 0)
    {
        _169 = _167 + _157;
    }
    int _171 = _169;
    int _173 = _171;
    int _175 = 4;
    int _177 = 0;
    int _179;
    int _486 = _54(_173, _175, _177, _179);
    int _181 = _486;
    int _183 = _171;
    if (_179 != 0)
    {
        _183 = _181 + _171;
    }
    int _185 = _183;
    int _187 = _185;
    int _189 = 8;
    int _191 = 0;
    int _193;
    int _498 = _54(_187, _189, _191, _193);
    int _195 = _498;
    int _197 = _185;
    if (_193 != 0)
    {
        _197 = _195 + _185;
    }
    int _199 = _197;
    int _201 = _199;
    int _203 = 16;
    int _205 = 0;
    int _207;
    int _510 = _54(_201, _203, _205, _207);
    int _209 = _510;
    int _211 = _199;
    if (_207 != 0)
    {
        _211 = _209 + _199;
    }
    if (_124)
    {
        int _213 = _211 << 2;
        gl_BuiltIn_5253[0] = 1;
        gp_s1_1._m0[int(uint(_213) >> uint(2))] = floatBitsToUint(_37[_65].x);
        gp_s1_1._m0[int(uint(_213 + 4) >> uint(2))] = floatBitsToUint(_37[_67].x);
        gp_s1_1._m0[int(uint(_213 + 8) >> uint(2))] = floatBitsToUint(_37[_72].x);
        return;
    }
    gl_BuiltIn_5253[0] = 0;
}

