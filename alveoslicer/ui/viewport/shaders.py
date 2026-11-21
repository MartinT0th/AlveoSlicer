"""GLSL shader sources and core OpenGL constants for the AlveoSlicer 3D viewport."""

from __future__ import annotations

# Core OpenGL enum constants (avoids external PyOpenGL dependency)
GL_LINES = 0x0001
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100

LIT_VERT_SRC = """
attribute vec3 a_pos;
attribute vec3 a_norm;
attribute vec3 a_color;
uniform mat4 u_mvp;
varying vec3 v_norm;
varying vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_norm = a_norm;
    v_color = a_color;
}
"""

LIT_FRAG_SRC = """
uniform vec2 u_alpha_v;
uniform vec3 u_view_dir;
varying vec3 v_norm;
varying vec3 v_color;
void main() {
    vec3 n = normalize(v_norm);
    vec3 l1 = normalize(vec3(0.45, -0.60, 0.85));
    vec3 l2 = normalize(vec3(-0.65, 0.55, 0.35));
    float d1 = max(dot(n, l1), 0.0);
    float d2 = max(dot(n, l2), 0.0);
    float hemi = 0.5 + 0.5 * n.z;
    vec3 half_v = normalize(l1 + normalize(u_view_dir));
    float spec = pow(max(dot(n, half_v), 0.0), 28.0) * 0.20;
    float shade = 0.32 + 0.16 * hemi + 0.50 * d1 + 0.22 * d2;
    vec3 col = clamp(v_color * shade + vec3(spec), 0.0, 1.0);
    gl_FragColor = vec4(col, u_alpha_v.x);
}
"""

LINE_VERT_SRC = """
attribute vec3 a_pos;
attribute vec3 a_color;
uniform mat4 u_mvp;
varying vec3 v_color;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_color = a_color;
}
"""

LINE_FRAG_SRC = """
uniform vec2 u_alpha_v;
varying vec3 v_color;
void main() {
    gl_FragColor = vec4(v_color, u_alpha_v.x);
}
"""
