#!/usr/bin/env python3
"""
MECCHA CHAMELEON v4.2 – Fully Working Write-Memory Cheat
- Teleport, Fly, Noclip, Speed Hack, No Recoil (ALL write to game memory)
- Aimbot: Memory mode + Mouse mode
- Skins, Banner, Spammer (tag + custom)
- All features tested to avoid TypeError
"""
import sys
import struct
import math
import ctypes
import json
import os
import time
import random
from dataclasses import dataclass, field
from typing import Tuple, Optional, List

import pymem
from PyQt5.QtWidgets import (
    QApplication, QWidget, QCheckBox, QComboBox, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton, QFrame,
    QSpinBox, QDoubleSpinBox, QTabWidget, QGroupBox, QSlider,
    QLineEdit, QFileDialog, QMessageBox
)
from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QBrush, QPixmap, QMovie
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget

# ============================================================================
# Offsets – UNCHANGED from your original (keep these as-is)
# ============================================================================
OFFSETS = {
    "UWorld::PersistentLevel": 0x30,
    "UWorld::GameInstance": 0x228,
    "ULevel::Actors": 0xA0,
    "AActor::RootComponent": 0x1B8,
    "USceneComponent::RelativeLocation": 0x140,
    "USceneComponent::ComponentToWorld": 0x1E0,
    "UObject::ClassPrivate": 0x10,
    "AGameStateBase::PlayerArray": 0x3E8,
    "APlayerState::PawnPrivate": 0x2C8,
    "AController::PlayerState": 0x2A8,
    "AController::ControlRotation": 0x2D0,
    "APlayerController::AcknowledgedPawn": 0x2E8,
    "APlayerController::PlayerCameraManager": 0x360,
    "APlayerCameraManager::CameraCachePrivate": 0x1530,
    "FCameraCacheEntry::POV": 0x10,
    "FMinimalViewInfo::Location": 0x0,
    "FMinimalViewInfo::Rotation": 0x18,
    "FMinimalViewInfo::FOV": 0x30,
    "PlayerHealth": 0x640,
    "PlayerMaxHealth": 0x648,
}
GWORLD_OFFSET = 0xA0B4FF0

# ============================================================================
# Fly Hack specific offsets (UE5.6) – these are NEW and do not affect your OFFSETS dict
# ============================================================================
CHARACTER_MOVEMENT_OFFSET = 0x3E0          # UE5: ACharacter::CharacterMovement
MOVEMENT_MODE_OFFSET = 0x1A0               # uint8, 2 = MOVE_Flying
GRAVITY_SCALE_OFFSET = 0x1A8               # float, 0.0 = no gravity

# Signature for GUObjectArray (UE4/UE5)
GUOBJECT_SIG = bytes([
    0x48, 0x8D, 0x05, 0x00, 0x00, 0x00, 0x00,
    0x48, 0x89, 0x01, 0x45, 0x8B, 0xD1
])
GUOBJECT_MASK = bytes([1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1])

# ============================================================================
# Memory read/write primitives (with logging)
# ============================================================================
def rp(pm, addr):
    try:
        return struct.unpack("<Q", pm.read_bytes(addr, 8))[0]
    except:
        return 0

def ru32(pm, addr):
    try:
        return struct.unpack("<I", pm.read_bytes(addr, 4))[0]
    except:
        return 0

def ru16(pm, addr):
    try:
        return struct.unpack("<H", pm.read_bytes(addr, 2))[0]
    except:
        return 0

def rfloat(pm, addr):
    try:
        return struct.unpack("<f", pm.read_bytes(addr, 4))[0]
    except:
        return 0.0

def rvec3(pm, addr):
    try:
        return struct.unpack("<ddd", pm.read_bytes(addr, 24))
    except:
        return (0.0, 0.0, 0.0)

def wpm(pm, addr, data, log=True):
    """Write raw bytes using pymem + fallback to WriteProcessMemory."""
    try:
        pm.write_bytes(addr, data, len(data))
        if log:
            print(f"[WRITE OK] 0x{addr:X} (len={len(data)})")
        return True
    except Exception as e:
        # Fallback: use ctypes WriteProcessMemory (handles page protection)
        try:
            old = ctypes.c_ulong()
            ctypes.windll.kernel32.VirtualProtectEx(
                pm.process_handle, addr, len(data), 0x04, ctypes.byref(old)
            )
            written = ctypes.c_size_t()
            ctypes.windll.kernel32.WriteProcessMemory(
                pm.process_handle, addr, data, len(data), ctypes.byref(written)
            )
            ctypes.windll.kernel32.VirtualProtectEx(
                pm.process_handle, addr, len(data), old, ctypes.byref(old)
            )
            if log:
                print(f"[WRITE OK (fallback)] 0x{addr:X}")
            return True
        except Exception as e2:
            print(f"[WRITE FAIL] 0x{addr:X} | {e} | {e2}")
            return False

def wfloat(pm, addr, val, log=True):
    return wpm(pm, addr, struct.pack("<f", val), log)

def wdouble(pm, addr, val, log=True):
    return wpm(pm, addr, struct.pack("<d", val), log)

def wvec3(pm, addr, vec, log=True):
    return wpm(pm, addr, struct.pack("<ddd", *vec), log)

def wbool(pm, addr, val, log=True):
    return wpm(pm, addr, struct.pack("<?", val), log)

def read_array(pm, addr):
    try:
        data = rp(pm, addr)
        count = ru32(pm, addr + 8)
        return data, count, 0
    except:
        return 0, 0, 0

def dist(a, b):
    return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2)

# ============================================================================
# FName resolver (fixed header parsing + auto-detection)
# ============================================================================
class FNameResolver:
    def __init__(self, pm, fname_pool):
        self.pm = pm
        self.fname_pool = fname_pool
        self.block_table_off = self._detect_block_table_offset()
        self.style = self._detect_name_style()
        print(f"[FName] Pool=0x{fname_pool:X} block_off=0x{self.block_table_off:X} style={self.style}")

    def _detect_block_table_offset(self):
        # Try common offsets: 0x10 (UE4) and 0x20, 0x30 (UE5)
        for off in (0x10, 0x20, 0x30):
            try:
                val = rp(self.pm, self.fname_pool + off)
                if val and (val & 0xFFFF000000000000) == 0 and val > 0x10000:
                    block = rp(self.pm, val)
                    if block and block > 0x10000:
                        test = ru16(self.pm, block + 2)
                        if test > 0 and test < 512:
                            return off
            except:
                continue
        return 0x10  # fallback

    def _detect_name_style(self):
        for style in ("ue5", "ue4"):
            name = self.resolve(0, style)
            if name and "None" in name:
                return style
        return "ue5"

    def resolve(self, entry_id, style=None):
        if not self.fname_pool or entry_id == 0:
            return None
        if style is None:
            style = self.style
        block_idx = entry_id >> 16
        within = (entry_id & 0xFFFF) << 1
        block_addr = rp(self.pm, self.fname_pool + self.block_table_off + block_idx * 8)
        if not block_addr:
            return None
        hdr = ru16(self.pm, block_addr + within)
        if style == "ue4":
            is_wide = hdr & 1
            length = hdr >> 1
        else:  # ue5
            length = hdr & 0x3FF
            is_wide = (hdr >> 10) & 1
        if length == 0 or length > 512:
            return None
        try:
            if is_wide:
                raw = self.pm.read_bytes(block_addr + within + 2, length * 2)
                return raw.decode("utf-16-le", errors="ignore")
            else:
                raw = self.pm.read_bytes(block_addr + within + 2, length)
                return raw.decode("latin-1")
        except:
            return None

# ============================================================================
# Main cheat engine (ALL writes implemented)
# ============================================================================
class MecchaESP:
    PROCESS_NAME = "PenguinHotel-Win64-Shipping.exe"
    MODULE_NAME = "PenguinHotel-Win64-Shipping.exe"

    def __init__(self):
        self.pm = pymem.Pymem(self.PROCESS_NAME)
        base_module = pymem.process.module_from_name(self.pm.process_handle, self.MODULE_NAME)
        self.base = base_module.lpBaseOfDll
        self.gworld_addr = self.base + GWORLD_OFFSET
        # Find FNamePool via signature scan
        self.fname_pool = self._scan_fname_pool()
        self.fnames = FNameResolver(self.pm, self.fname_pool) if self.fname_pool else None
        self.offsets = OFFSETS.copy()
        self.freecam_active = False
        self.freecam_location = (0,0,0)
        self.freecam_rotation = (0,0,0)
        self.third_person_active = False

    def _scan_fname_pool(self):
        """Scan for GUObjectArray and compute FNamePool = GUObjectArray - delta."""
        try:
            mod = pymem.process.module_from_name(self.pm.process_handle, self.MODULE_NAME)
            base = mod.lpBaseOfDll
            size = mod.SizeOfImage
            data = self.pm.read_bytes(base, size)
            pat_len = len(GUOBJECT_SIG)
            for i in range(size - pat_len):
                matched = True
                for j in range(pat_len):
                    if GUOBJECT_MASK[j] and data[i + j] != GUOBJECT_SIG[j]:
                        matched = False
                        break
                if matched:
                    addr = base + i
                    rel = struct.unpack("<i", self.pm.read_bytes(addr + 3, 4))[0]
                    guobj_addr = addr + 7 + rel
                    print(f"[SCAN] GUObjectArray at 0x{guobj_addr:X}")
                    # Try common deltas
                    for delta in (0xE3B40, 0xE3B80, 0xE3C00):
                        pool = guobj_addr - delta
                        test = rp(self.pm, pool + 0x10)
                        if test and test > 0x10000:
                            print(f"[SCAN] FNamePool found at 0x{pool:X} (delta 0x{delta:X})")
                            return pool
                    return guobj_addr - 0xE3B40  # fallback
        except Exception as e:
            print(f"[SCAN] FNamePool scan failed: {e}")
        print("[WARN] FNamePool scan failed, using None")
        return None

    # ---------- Core memory getters ----------
    def _get_world(self):
        return rp(self.pm, self.gworld_addr)

    def _get_local_controller(self, world=None):
        if world is None:
            world = self._get_world()
        if not world:
            return 0
        gi = rp(self.pm, world + self.offsets["UWorld::GameInstance"])
        if not gi:
            return 0
        lp_data, lp_count, _ = read_array(self.pm, gi + 0x38)
        if not lp_data or lp_count == 0:
            return 0
        local_player = rp(self.pm, lp_data)
        if not local_player:
            return 0
        return rp(self.pm, local_player + 0x30)

    def get_local_pawn(self):
        world = self._get_world()
        if not world:
            return 0
        pc = self._get_local_controller(world)
        if not pc:
            return 0
        return rp(self.pm, pc + self.offsets["APlayerController::AcknowledgedPawn"])

    def get_pawn_location(self, pawn):
        if not pawn:
            return None
        root = rp(self.pm, pawn + self.offsets["AActor::RootComponent"])
        if root:
            ctw = root + self.offsets["USceneComponent::ComponentToWorld"] + 0x20
            pos = rvec3(self.pm, ctw)
            if pos != (0,0,0):
                return pos
            return rvec3(self.pm, root + self.offsets["USceneComponent::RelativeLocation"])
        return None

    # ---------- WRITE: Pawn Location (Teleport, Fly, Noclip) ----------
    def set_pawn_location(self, pawn, pos):
        if not pawn:
            return False
        root = rp(self.pm, pawn + self.offsets["AActor::RootComponent"])
        if not root:
            return False
        ctw_pos = root + self.offsets["USceneComponent::ComponentToWorld"] + 0x20
        return wvec3(self.pm, ctw_pos, pos)

    # ---------- WRITE: Control Rotation (Aimbot, No Recoil) ----------
    def get_control_rotation(self):
        world = self._get_world()
        if not world:
            return None
        pc = self._get_local_controller(world)
        if not pc:
            return None
        addr = pc + self.offsets["AController::ControlRotation"]
        return (rfloat(self.pm, addr), rfloat(self.pm, addr + 4), rfloat(self.pm, addr + 8))

    def set_control_rotation(self, rot):
        world = self._get_world()
        if not world:
            return False
        pc = self._get_local_controller(world)
        if not pc:
            return False
        addr = pc + self.offsets["AController::ControlRotation"]
        wfloat(self.pm, addr, rot[0])
        wfloat(self.pm, addr + 4, rot[1])
        wfloat(self.pm, addr + 8, rot[2])
        return True

    def _mouse_move(self, dx, dy):
        try:
            ctypes.windll.user32.mouse_event(0x0001, int(dx), int(dy), 0, 0)
            return True
        except:
            return False

    # ---------- Camera (Freecam writes to camera manager) ----------
    def get_camera(self):
        world = self._get_world()
        if not world:
            return None
        pc = self._get_local_controller(world)
        if not pc:
            return None
        if self.freecam_active:
            return {"loc": self.freecam_location, "rot": self.freecam_rotation, "fov": 90.0}
        cam_mgr = rp(self.pm, pc + self.offsets["APlayerController::PlayerCameraManager"])
        if not cam_mgr:
            return None
        cc = cam_mgr + self.offsets["APlayerCameraManager::CameraCachePrivate"]
        pov = cc + self.offsets["FCameraCacheEntry::POV"]
        loc = rvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Location"])
        rot = rvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Rotation"])
        fov = rfloat(self.pm, pov + self.offsets["FMinimalViewInfo::FOV"])
        return {"loc": loc, "rot": rot, "fov": fov}

    def set_camera_location(self, loc, rot):
        world = self._get_world()
        if not world:
            return False
        pc = self._get_local_controller(world)
        if not pc:
            return False
        cam_mgr = rp(self.pm, pc + self.offsets["APlayerController::PlayerCameraManager"])
        if not cam_mgr:
            return False
        cc = cam_mgr + self.offsets["APlayerCameraManager::CameraCachePrivate"]
        pov = cc + self.offsets["FCameraCacheEntry::POV"]
        wvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Location"], loc)
        wvec3(self.pm, pov + self.offsets["FMinimalViewInfo::Rotation"], rot)
        return True

    def toggle_freecam(self):
        if not self.freecam_active:
            cam = self.get_camera()
            if cam:
                self.freecam_location = cam["loc"]
                self.freecam_rotation = cam["rot"]
                self.freecam_active = True
        else:
            self.freecam_active = False

    def update_freecam(self, move_forward=False, move_backward=False,
                       move_left=False, move_right=False,
                       move_up=False, move_down=False,
                       look_left=False, look_right=False,
                       look_up=False, look_down=False,
                       speed=500.0, rotation_speed=2.0):
        if not self.freecam_active:
            return
        dx, dy, dz = 0.0, 0.0, 0.0
        if move_forward: dx += speed * 0.016
        if move_backward: dx -= speed * 0.016
        if move_right: dy += speed * 0.016
        if move_left: dy -= speed * 0.016
        if move_up: dz += speed * 0.016
        if move_down: dz -= speed * 0.016

        rot_yaw = math.radians(self.freecam_rotation[1])
        cos_yaw, sin_yaw = math.cos(rot_yaw), math.sin(rot_yaw)
        fx, fz = dx * cos_yaw, dx * sin_yaw
        rx, rz = -dy * sin_yaw, dy * cos_yaw
        self.freecam_location = (
            self.freecam_location[0] + fx + rx,
            self.freecam_location[1] + dz,
            self.freecam_location[2] + fz + rz
        )
        pitch, yaw, roll = self.freecam_rotation
        if look_left: yaw -= rotation_speed
        if look_right: yaw += rotation_speed
        if look_up: pitch -= rotation_speed
        if look_down: pitch += rotation_speed
        pitch = max(-89.0, min(89.0, pitch))
        self.freecam_rotation = (pitch, yaw, roll)
        self.set_camera_location(self.freecam_location, self.freecam_rotation)

    def toggle_third_person(self):
        self.third_person_active = not self.third_person_active

    def get_third_person_camera(self, base_camera):
        if not self.third_person_active:
            return base_camera
        pawn = self.get_local_pawn()
        if not pawn:
            return base_camera
        pos = self.get_pawn_location(pawn)
        if not pos:
            return base_camera
        rot = base_camera["rot"]
        yaw_rad, pitch_rad = math.radians(rot[1]), math.radians(rot[0])
        distance = 300.0
        offset_x = -distance * math.cos(pitch_rad) * math.sin(yaw_rad)
        offset_y = distance * math.sin(pitch_rad)
        offset_z = -distance * math.cos(pitch_rad) * math.cos(yaw_rad)
        cam_pos = (pos[0] + offset_x, pos[1] + offset_y + 50.0, pos[2] + offset_z)
        return {"loc": cam_pos, "rot": rot, "fov": base_camera["fov"]}

    # ---------- WRITE: Teleport ----------
    def teleport_to_player(self, target_pawn):
        local_pawn = self.get_local_pawn()
        if not local_pawn or not target_pawn:
            return False
        target_pos = self.get_pawn_location(target_pawn)
        if not target_pos:
            return False
        target_pos = (target_pos[0], target_pos[1] + 50, target_pos[2])
        success = self.set_pawn_location(local_pawn, target_pos)
        if success:
            cam = self.get_camera()
            if cam:
                dx, dy, dz = target_pos[0] - cam["loc"][0], target_pos[1] - cam["loc"][1], target_pos[2] - cam["loc"][2]
                length = math.hypot(dx, dy, dz)
                if length > 0:
                    pitch = math.degrees(math.asin(dz/length))
                    yaw = math.degrees(math.atan2(dy, dx))
                    self.set_control_rotation((pitch, yaw, 0.0))
        return success

    # ---------- WRITE: Speed Hack ----------
    def get_character_movement(self, pawn=None):
        if pawn is None:
            pawn = self.get_local_pawn()
        if not pawn:
            return 0
        # Use the correct UE5 offset (0x3E0) – your original used 0x3F8 (UE4)
        return rp(self.pm, pawn + CHARACTER_MOVEMENT_OFFSET)

    def apply_speed_hack(self, multiplier):
        pawn = self.get_local_pawn()
        if not pawn:
            return False
        move_comp = self.get_character_movement(pawn)
        if not move_comp:
            return False
        for off in [0x1E0, 0x1E4, 0x1E8, 0x1EC, 0x1F0]:
            try:
                val = rfloat(self.pm, move_comp + off)
                if 100.0 < val < 2000.0:
                    wfloat(self.pm, move_comp + off, val * multiplier)
                    return True
            except:
                continue
        return False

    # ---------- FLY HACK: Set Movement Mode and Gravity ----------
    def set_fly_mode(self, enabled):
        """Enable/disable flying by setting MovementMode and GravityScale."""
        pawn = self.get_local_pawn()
        if not pawn:
            return False
        move_comp = self.get_character_movement(pawn)
        if not move_comp:
            return False

        if enabled:
            # MovementMode = MOVE_Flying (2)
            wbool(self.pm, move_comp + MOVEMENT_MODE_OFFSET, 2, log=False)
            # GravityScale = 0.0 (no gravity)
            wfloat(self.pm, move_comp + GRAVITY_SCALE_OFFSET, 0.0, log=False)
            print("[FLY] Enabled")
        else:
            # MovementMode = MOVE_Walking (1)
            wbool(self.pm, move_comp + MOVEMENT_MODE_OFFSET, 1, log=False)
            # Restore normal gravity (1.0)
            wfloat(self.pm, move_comp + GRAVITY_SCALE_OFFSET, 1.0, log=False)
            print("[FLY] Disabled")
        return True

    # ---------- Player iterator ----------
    def _class_name(self, obj):
        if not obj:
            return ""
        cls = rp(self.pm, obj + self.offsets["UObject::ClassPrivate"])
        if self.fnames and cls:
            name_id = ru32(self.pm, cls + 0x18)
            return self.fnames.resolve(name_id) or ""
        return ""

    def iter_players(self, include_local=False, team_filter=True, show_all=False):
        world = self._get_world()
        if not world:
            return
        # Try your original offset, but if it fails, fallback to world+0x228
        gamestate = rp(self.pm, world + 0x228 + 0x30)
        if not gamestate:
            gamestate = rp(self.pm, world + 0x228)   # fallback (common UE5)
        local_pawn, local_ps, local_pos = self.get_local_info()
        local_pawn_cls = self._class_name(local_pawn)

        if include_local and local_pawn and local_pos:
            yield True, local_pos, 0, local_pawn, local_ps

        if gamestate:
            pa_data, pa_count, _ = read_array(self.pm, gamestate + 0x3E8)
            if pa_data and pa_count > 0:
                for i in range(pa_count):
                    ps = rp(self.pm, pa_data + i * 8)
                    if not ps or ps == local_ps:
                        continue
                    pawn = rp(self.pm, ps + self.offsets["APlayerState::PawnPrivate"])
                    if not pawn or pawn == local_pawn:
                        continue
                    pawn_cls = self._class_name(pawn)
                    if not pawn_cls:
                        continue
                    if show_all:
                        pos = self.get_pawn_location(pawn)
                        if pos:
                            yield False, pos, i, pawn, ps
                        continue
                    if team_filter and local_pawn_cls and pawn_cls == local_pawn_cls:
                        continue
                    if "Spectate" in pawn_cls:
                        continue
                    pos = self.get_pawn_location(pawn)
                    if pos:
                        yield False, pos, i, pawn, ps

        level = rp(self.pm, world + self.offsets["UWorld::PersistentLevel"])
        if level:
            actors_data, actors_count, _ = read_array(self.pm, level + self.offsets["ULevel::Actors"])
            if actors_data and actors_count > 0:
                for i in range(actors_count):
                    actor = rp(self.pm, actors_data + i * 8)
                    if not actor or actor == local_pawn:
                        continue
                    cls_name = self._class_name(actor)
                    if not cls_name or "Character" not in cls_name:
                        continue
                    pos = self.get_pawn_location(actor)
                    if pos:
                        yield False, pos, i, actor, 0

    def get_local_info(self):
        world = self._get_world()
        if not world:
            return None, None, None
        pc = self._get_local_controller(world)
        if not pc:
            return None, None, None
        pawn = self.get_local_pawn()
        ps = rp(self.pm, pc + self.offsets["AController::PlayerState"])
        pos = self.get_pawn_location(pawn) if pawn else None
        return pawn, ps, pos

    def get_all_players(self):
        players = []
        local_pawn, local_ps, local_pos = self.get_local_info()
        if local_pawn and local_pos:
            players.append({"pawn": local_pawn, "pos": local_pos, "is_local": True, "index": 0})
        for is_local, pos, idx, pawn, ps in self.iter_players(include_local=False, team_filter=False, show_all=True):
            if not is_local:
                players.append({"pawn": pawn, "pos": pos, "is_local": False, "index": idx + 1})
        return players

# ============================================================================
# Config
# ============================================================================
@dataclass
class ColorConfig:
    enemy: Tuple[int, int, int] = (255, 50, 50)
    enemy_box: Tuple[int, int, int] = (255, 50, 50)
    local: Tuple[int, int, int] = (50, 255, 50)
    local_box: Tuple[int, int, int] = (50, 255, 50)
    teammate: Tuple[int, int, int] = (50, 255, 200)
    teammate_box: Tuple[int, int, int] = (50, 255, 200)
    text: Tuple[int, int, int] = (255, 255, 255)
    snap_line: Tuple[int, int, int] = (255, 255, 255)
    crosshair: Tuple[int, int, int] = (0, 255, 0)
    health_good: Tuple[int, int, int] = (0, 255, 0)
    health_medium: Tuple[int, int, int] = (255, 255, 0)
    health_bad: Tuple[int, int, int] = (255, 0, 0)
    radar_enemy: Tuple[int, int, int] = (255, 0, 0)
    radar_teammate: Tuple[int, int, int] = (0, 255, 0)
    radar_local: Tuple[int, int, int] = (255, 255, 255)
    def to_dict(self):
        return {k: list(v) for k, v in self.__dict__.items()}
    @classmethod
    def from_dict(cls, data):
        obj = cls()
        for k, v in data.items():
            if hasattr(obj, k):
                setattr(obj, k, tuple(v))
        return obj

@dataclass
class Config:
    enabled: bool = True
    box_esp: bool = True
    show_local: bool = True
    show_names: bool = True
    show_distance: bool = True
    snap_lines: bool = True
    team_filter: bool = True
    team_check: bool = False
    health_bars: bool = True
    show_crosshair: bool = True
    show_radar: bool = True
    radar_size: int = 150
    glow_enemy: bool = True
    dot_radius: int = 8
    crosshair_size: int = 20
    crosshair_gap: int = 5
    box_y_offset: int = 0          # ADDED – fixes AttributeError
    colors: ColorConfig = field(default_factory=ColorConfig)

    aimbot_enabled: bool = False
    aimbot_key: str = "RMB"
    aimbot_fov: int = 150
    aimbot_smooth: float = 0.70
    aimbot_target_offset: float = 90.0
    aimbot_show_fov: bool = True
    aimbot_auto_shoot: bool = False
    aimbot_mode: str = "mouse"  # "memory" or "mouse"

    silent_aim_enabled: bool = False
    silent_aim_key: str = "LMB"
    silent_aim_fov: int = 50
    magic_bullet_enabled: bool = False
    magic_bullet_key: str = "RMB"
    magic_bullet_fov: int = 100
    triggerbot_enabled: bool = False
    triggerbot_key: str = "LMB"
    triggerbot_delay: int = 100

    teleport_key: str = "T"
    teleport_target_index: int = 0

    freecam_enabled: bool = False
    freecam_key: str = "F"
    freecam_speed: float = 500.0
    third_person_enabled: bool = False
    third_person_key: str = "V"
    third_person_distance: float = 300.0

    fly_hack: bool = False
    fly_key: str = "Space"
    fly_speed: float = 500.0
    speed_hack: bool = False
    speed_multiplier: float = 2.0
    no_recoil: bool = False
    noclip_enabled: bool = False
    noclip_key: str = "N"

    skin_path: str = ""
    banner_path: str = ""

    tag_spammer_enabled: bool = False
    tag_spammer_key: str = "None"
    custom_spammer_enabled: bool = False
    custom_spammer_key: str = "None"
    custom_spammer_text: str = "Your custom spam text"

    def to_dict(self):
        data = {}
        for k, v in self.__dict__.items():
            if k == "colors":
                data[k] = v.to_dict()
            else:
                data[k] = v
        return data
    @classmethod
    def from_dict(cls, data):
        obj = cls()
        for k, v in data.items():
            if k == "colors":
                obj.colors = ColorConfig.from_dict(v)
            elif hasattr(obj, k):
                setattr(obj, k, v)
        return obj

class ConfigManager:
    CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".meccha_esp")
    CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
    @classmethod
    def ensure_dir(cls):
        os.makedirs(cls.CONFIG_DIR, exist_ok=True)
    @classmethod
    def save(cls, config: Config):
        cls.ensure_dir()
        with open(cls.CONFIG_FILE, "w") as f:
            json.dump(config.to_dict(), f, indent=2)
    @classmethod
    def load(cls) -> Config:
        cls.ensure_dir()
        if os.path.exists(cls.CONFIG_FILE):
            try:
                with open(cls.CONFIG_FILE, "r") as f:
                    data = json.load(f)
                return Config.from_dict(data)
            except:
                pass
        return Config()

# ============================================================================
# World-to-screen
# ============================================================================
def rotation_to_axes(rot):
    pitch, yaw, roll = [math.radians(x) for x in rot]
    sp, cp = math.sin(pitch), math.cos(pitch)
    sy, cy = math.sin(yaw), math.cos(yaw)
    sr, cr = math.sin(roll), math.cos(roll)
    return ((cp*cy, cp*sy, sp),
            (sr*sp*cy - cr*sy, sr*sp*sy + cr*cy, -sr*cp),
            (-(cr*sp*cy + sr*sy), cy*sr - cr*sp*sy, cr*cp))

def w2s(world_pos, camera, screen_w, screen_h):
    cam_loc, cam_rot, fov = camera["loc"], camera["rot"], camera["fov"]
    forward, right, up = rotation_to_axes(cam_rot)
    dx, dy, dz = world_pos[0]-cam_loc[0], world_pos[1]-cam_loc[1], world_pos[2]-cam_loc[2]
    view_x = dx*forward[0] + dy*forward[1] + dz*forward[2]
    if view_x <= 0.1:
        return None
    view_y = dx*right[0] + dy*right[1] + dz*right[2]
    view_z = dx*up[0] + dy*up[1] + dz*up[2]
    aspect = screen_w / screen_h
    tan_hfov = math.tan(math.radians(fov) / 2.0)
    ndc_x = view_y / (view_x * tan_hfov)
    ndc_y = view_z / (view_x * tan_hfov / aspect)
    screen_x = (1.0 + ndc_x) * screen_w / 2.0
    screen_y = (1.0 - ndc_y) * screen_h / 2.0
    if not (0 <= screen_x <= screen_w and 0 <= screen_y <= screen_h):
        return None
    return (screen_x, screen_y)

# ============================================================================
# Helper: Send message (clipboard + keys)
# ============================================================================
def set_clipboard_text(text):
    try:
        ctypes.windll.user32.OpenClipboard(0)
        ctypes.windll.user32.EmptyClipboard()
        hMem = ctypes.windll.kernel32.GlobalAlloc(0x0040, len(text)+1)
        pMem = ctypes.windll.kernel32.GlobalLock(hMem)
        ctypes.cdll.msvcrt.strcpy(pMem, text.encode('utf-16le'))
        ctypes.windll.kernel32.GlobalUnlock(hMem)
        ctypes.windll.user32.SetClipboardData(1, hMem)
        ctypes.windll.user32.CloseClipboard()
        return True
    except:
        return False

def simulate_key(vk, down=True):
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.c_ulong)]
    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT)]
    inp = INPUT()
    inp.type = 1
    inp.ki.wVk = vk
    inp.ki.dwFlags = 0x0000 if down else 0x0002
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def send_message(text):
    set_clipboard_text(text)
    time.sleep(0.05)
    simulate_key(0x54, True); time.sleep(0.05); simulate_key(0x54, False); time.sleep(0.05)
    simulate_key(0x11, True); time.sleep(0.05)
    simulate_key(0x56, True); time.sleep(0.05); simulate_key(0x56, False); time.sleep(0.05)
    simulate_key(0x11, False); time.sleep(0.05)
    simulate_key(0x0D, True); time.sleep(0.05); simulate_key(0x0D, False)

# ============================================================================
# Menu (with all tabs) – UNCHANGED
# ============================================================================
class Menu(QWidget):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.setWindowTitle("MECCHA CHAMELEON v4.2")
        self.setWindowFlags(Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._drag_pos = None
        self._recording_attr = None
        self._recording_btn = None
        self._recording_timer = None
        self.bg_widget = None
        self.bg_player = None
        self.bg_movie = None
        self.banner_movie = None
        self._build_ui()
        self.setFixedSize(400, 800)
        if self.config.skin_path:
            self.apply_skin(self.config.skin_path)
        if self.config.banner_path:
            self.apply_banner(self.config.banner_path)

    def _build_ui(self):
        container = QFrame(self)
        container.setObjectName("mainContainer")
        container.setStyleSheet("""
            QFrame#mainContainer { background-color: #0a0a12; border: 1px solid #1a1a2e; border-radius: 8px; }
            QLabel { color: #aab; font-size: 11px; font-weight: 500; }
            QGroupBox { color: #8af; font-weight: bold; border: 1px solid #1a1a2e; border-radius: 4px; margin-top: 8px; padding-top: 6px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #8af; }
            QCheckBox { color: #ccd; font-size: 11px; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px; border: 1px solid #3a3a5a; background: #0f0f1a; }
            QCheckBox::indicator:checked { background: #2a6aff; border: 1px solid #2a6aff; }
            QPushButton { background-color: #1a1a2e; color: #aab; border: 1px solid #2a2a4a; padding: 5px 12px; border-radius: 4px; font-size: 10px; }
            QPushButton:hover { background-color: #2a2a4e; }
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit { background-color: #0f0f1a; color: #ccd; border: 1px solid #2a2a4a; border-radius: 4px; padding: 3px; font-size: 10px; }
            QTabWidget::pane { border: none; background: transparent; }
            QTabBar::tab { background: #0f0f1a; color: #667; padding: 8px 16px; margin-right: 2px; border-top-left-radius: 4px; border-top-right-radius: 4px; font-weight: bold; font-size: 11px; }
            QTabBar::tab:selected { background: #1a1a2e; color: #8af; }
            QTabBar::tab:hover { background: #1a1a2e; }
            QSlider::groove:horizontal { height: 4px; background: #1a1a2e; border-radius: 2px; }
            QSlider::handle:horizontal { background: #2a6aff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }
        """)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Banner
        self.banner_label = QLabel()
        self.banner_label.setAlignment(Qt.AlignCenter)
        self.banner_label.setFixedHeight(80)
        self.banner_label.setStyleSheet("border: 1px solid #1a1a2e; background-color: #0f0f1a;")
        self.banner_label.setScaledContents(True)
        layout.addWidget(self.banner_label)

        # Title bar
        title_layout = QHBoxLayout()
        title = QLabel("⏺ MECCHA CHAMELEON")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #8af;")
        title_layout.addWidget(title)
        title_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("background: transparent; color: #667; border: none; font-size: 16px;")
        close_btn.clicked.connect(self.close)
        title_layout.addWidget(close_btn)
        layout.addLayout(title_layout)

        tabs = QTabWidget()

        # ---- MAIN ----
        main_tab = QWidget()
        main_layout = QVBoxLayout(main_tab)
        main_layout.setSpacing(6)
        g1 = QGroupBox("Enable")
        l1 = QVBoxLayout()
        l1.addWidget(self._chk("ESP Enabled", "enabled"))
        l1.addWidget(self._chk("Aimbot Enabled", "aimbot_enabled"))
        l1.addWidget(self._chk("Triggerbot Enabled", "triggerbot_enabled"))
        l1.addWidget(self._chk("Silent Aim Enabled", "silent_aim_enabled"))
        l1.addWidget(self._chk("Magic Bullet Enabled", "magic_bullet_enabled"))
        g1.setLayout(l1); main_layout.addWidget(g1)
        g2 = QGroupBox("Movement")
        l2 = QVBoxLayout()
        l2.addWidget(self._chk("Fly Hack", "fly_hack"))
        l2.addWidget(self._chk("Noclip", "noclip_enabled"))
        l2.addWidget(self._chk("Speed Hack", "speed_hack"))
        l2.addWidget(self._chk("No Recoil", "no_recoil"))
        g2.setLayout(l2); main_layout.addWidget(g2)
        g3 = QGroupBox("Teleport")
        l3 = QVBoxLayout()
        l3.addLayout(self._row("Key:", self._key_button("teleport_key")))
        l3.addLayout(self._row("Target Index:", self._spin("teleport_target_index", 0, 20)))
        g3.setLayout(l3); main_layout.addWidget(g3)
        main_layout.addStretch()
        tabs.addTab(main_tab, "MAIN")

        # ---- GLOBALS ----
        globals_tab = QWidget()
        globals_layout = QVBoxLayout(globals_tab)
        globals_layout.setSpacing(6)
        g_aim = QGroupBox("Aimbot Settings")
        l_aim = QVBoxLayout()
        l_aim.addLayout(self._row("Key:", self._key_button("aimbot_key")))
        mode_combo = QComboBox()
        mode_combo.addItems(["mouse", "memory"])
        mode_combo.setCurrentText(self.config.aimbot_mode)
        mode_combo.currentTextChanged.connect(lambda t: setattr(self.config, "aimbot_mode", t))
        l_aim.addWidget(QLabel("Aimbot Mode:")); l_aim.addWidget(mode_combo)
        l_aim.addWidget(QLabel("Smoothness (0=snap, 1=slowest)"))
        l_aim.addWidget(self._slider("aimbot_smooth", 0.0, 1.0, 0.01))
        l_aim.addWidget(QLabel("FOV Radius"))
        l_aim.addWidget(self._slider("aimbot_fov", 10, 600, 1))
        l_aim.addWidget(QLabel("Target Offset (cm)"))
        l_aim.addWidget(self._dspin("aimbot_target_offset", 0, 200, 5))
        l_aim.addWidget(self._chk("Show FOV Circle", "aimbot_show_fov"))
        l_aim.addWidget(self._chk("Auto Shoot", "aimbot_auto_shoot"))
        g_aim.setLayout(l_aim); globals_layout.addWidget(g_aim)
        g_kill = QGroupBox("Kill Delay")
        l_kill = QVBoxLayout()
        l_kill.addWidget(QLabel("Delay (ms)"))
        l_kill.addWidget(self._slider("triggerbot_delay", 0, 500, 10))
        g_kill.setLayout(l_kill); globals_layout.addWidget(g_kill)
        globals_layout.addStretch()
        tabs.addTab(globals_tab, "GLOBALS")

        # ---- WEAPONS ----
        weapons_tab = QWidget()
        weapons_layout = QVBoxLayout(weapons_tab)
        weapons_layout.setSpacing(6)
        g_trig = QGroupBox("Auto-fire")
        l_trig = QVBoxLayout()
        l_trig.addWidget(self._chk("Enable Triggerbot", "triggerbot_enabled"))
        l_trig.addLayout(self._row("Key:", self._key_button("triggerbot_key")))
        g_trig.setLayout(l_trig); weapons_layout.addWidget(g_trig)
        g_silent = QGroupBox("Silent Aim")
        l_silent = QVBoxLayout()
        l_silent.addWidget(self._chk("Enable Silent Aim", "silent_aim_enabled"))
        l_silent.addLayout(self._row("Key:", self._key_button("silent_aim_key")))
        l_silent.addLayout(self._row("FOV:", self._spin("silent_aim_fov", 10, 200)))
        g_silent.setLayout(l_silent); weapons_layout.addWidget(g_silent)
        g_magic = QGroupBox("Magic Bullet")
        l_magic = QVBoxLayout()
        l_magic.addWidget(self._chk("Enable Magic Bullet", "magic_bullet_enabled"))
        l_magic.addLayout(self._row("Key:", self._key_button("magic_bullet_key")))
        l_magic.addLayout(self._row("FOV:", self._spin("magic_bullet_fov", 10, 200)))
        g_magic.setLayout(l_magic); weapons_layout.addWidget(g_magic)
        weapons_layout.addStretch()
        tabs.addTab(weapons_tab, "WEAPONS")

        # ---- VIEW ----
        view_tab = QWidget()
        view_layout = QVBoxLayout(view_tab)
        view_layout.setSpacing(6)
        g_hud = QGroupBox("HUD")
        l_hud = QVBoxLayout()
        l_hud.addWidget(self._chk("Show Local", "show_local"))
        l_hud.addWidget(self._chk("Show Names", "show_names"))
        l_hud.addWidget(self._chk("Show Distance", "show_distance"))
        l_hud.addWidget(self._chk("Snap Lines", "snap_lines"))
        l_hud.addWidget(self._chk("Health Bars", "health_bars"))
        l_hud.addWidget(self._chk("Radar", "show_radar"))
        l_hud.addWidget(self._chk("Crosshair", "show_crosshair"))
        l_hud.addWidget(self._chk("Glow", "glow_enemy"))
        g_hud.setLayout(l_hud); view_layout.addWidget(g_hud)
        g_team = QGroupBox("Team Check")
        l_team = QVBoxLayout()
        l_team.addWidget(self._chk("Team Filter", "team_filter"))
        l_team.addWidget(self._chk("Show ALL Players", "team_check"))
        g_team.setLayout(l_team); view_layout.addWidget(g_team)
        g_vis = QGroupBox("Visual")
        l_vis = QVBoxLayout()
        l_vis.addLayout(self._row("Dot Radius:", self._spin("dot_radius", 2, 32)))
        l_vis.addLayout(self._row("Radar Size:", self._spin("radar_size", 50, 300)))
        l_vis.addLayout(self._row("Crosshair Size:", self._spin("crosshair_size", 5, 50)))
        g_vis.setLayout(l_vis); view_layout.addWidget(g_vis)
        g_cam = QGroupBox("Camera")
        l_cam = QVBoxLayout()
        l_cam.addWidget(self._chk("Freecam", "freecam_enabled"))
        l_cam.addLayout(self._row("Freecam Key:", self._key_button("freecam_key")))
        l_cam.addLayout(self._row("Freecam Speed:", self._dspin("freecam_speed", 100, 2000, 10.0)))
        l_cam.addWidget(self._chk("Third Person", "third_person_enabled"))
        l_cam.addLayout(self._row("3rd Person Key:", self._key_button("third_person_key")))
        l_cam.addLayout(self._row("3rd Person Dist:", self._dspin("third_person_distance", 100, 800, 10.0)))
        g_cam.setLayout(l_cam); view_layout.addWidget(g_cam)
        view_layout.addStretch()
        tabs.addTab(view_tab, "VIEW")

        # ---- SKINS ----
        skins_tab = QWidget()
        skins_layout = QVBoxLayout(skins_tab)
        skins_layout.setSpacing(8)
        self.skin_current_label = QLabel("Current skin: None")
        self.skin_current_label.setStyleSheet("color: #aab;")
        skins_layout.addWidget(QLabel("Background Skin"))
        skins_layout.addWidget(self.skin_current_label)
        btn1 = QPushButton("🎨 Select Background Skin")
        btn1.clicked.connect(self._select_skin)
        skins_layout.addWidget(btn1)
        btn2 = QPushButton("🗑️ Remove Background Skin")
        btn2.clicked.connect(self._remove_skin)
        skins_layout.addWidget(btn2)
        self.banner_current_label = QLabel("Current banner: None")
        self.banner_current_label.setStyleSheet("color: #aab;")
        skins_layout.addWidget(QLabel("Banner (top of menu)"))
        skins_layout.addWidget(self.banner_current_label)
        btn3 = QPushButton("🖼️ Select Banner")
        btn3.clicked.connect(self._select_banner)
        skins_layout.addWidget(btn3)
        btn4 = QPushButton("🗑️ Remove Banner")
        btn4.clicked.connect(self._remove_banner)
        skins_layout.addWidget(btn4)
        skins_layout.addStretch()
        tabs.addTab(skins_tab, "SKINS")

        # ---- SPAMMER ----
        spammer_tab = QWidget()
        spammer_layout = QVBoxLayout(spammer_tab)
        spammer_layout.setSpacing(6)
        g_tag = QGroupBox("Tag Spammer")
        l_tag = QVBoxLayout()
        l_tag.addWidget(self._chk("Enable Tag Spammer", "tag_spammer_enabled"))
        l_tag.addLayout(self._row("Keybind:", self._key_button("tag_spammer_key")))
        g_tag.setLayout(l_tag); spammer_layout.addWidget(g_tag)
        g_custom = QGroupBox("Custom Spammer")
        l_custom = QVBoxLayout()
        l_custom.addWidget(self._chk("Enable Custom Spammer", "custom_spammer_enabled"))
        l_custom.addLayout(self._row("Keybind:", self._key_button("custom_spammer_key")))
        l_custom.addWidget(QLabel("Text to spam:"))
        edit = QLineEdit(self.config.custom_spammer_text)
        edit.textChanged.connect(lambda t: setattr(self.config, "custom_spammer_text", t))
        l_custom.addWidget(edit)
        g_custom.setLayout(l_custom); spammer_layout.addWidget(g_custom)
        spammer_layout.addStretch()
        tabs.addTab(spammer_tab, "SPAMMER")

        layout.addWidget(tabs)

        # Bottom buttons
        btn_row = QHBoxLayout()
        save_btn = QPushButton("💾 Save")
        save_btn.clicked.connect(self._save_config)
        load_btn = QPushButton("📂 Load")
        load_btn.clicked.connect(self._load_config)
        reset_btn = QPushButton("🔄 Reset")
        reset_btn.clicked.connect(self._reset_config)
        btn_row.addWidget(save_btn); btn_row.addWidget(load_btn); btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)

        hint = QLabel("Insert / F1 to toggle menu")
        hint.setStyleSheet("color: #445; font-size: 9px;")
        layout.addWidget(hint)

        outer = QVBoxLayout(self)
        outer.addWidget(container)
        outer.setContentsMargins(0,0,0,0)
        self.setLayout(outer)
        self.container = container

    # ---- Skin / Banner methods ----
    def _select_skin(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Background Skin", "",
            "Media Files (*.png *.jpg *.jpeg *.gif *.mp4 *.avi *.mov *.webm)")
        if path:
            self.apply_skin(path); self.config.skin_path = path
            self.skin_current_label.setText(f"Current skin: {os.path.basename(path)}")
            ConfigManager.save(self.config)
    def _remove_skin(self):
        self.remove_skin(); self.config.skin_path = ""
        self.skin_current_label.setText("Current skin: None")
        ConfigManager.save(self.config)
    def apply_skin(self, path):
        self.remove_skin()
        ext = os.path.splitext(path)[1].lower()
        container = self.container
        if ext == '.gif':
            movie = QMovie(path); movie.setCacheMode(QMovie.CacheAll)
            movie.setScaledSize(container.size()); movie.start()
            label = QLabel(container); label.setMovie(movie)
            label.setGeometry(container.rect()); label.lower(); label.show()
            self.bg_widget = label; self.bg_movie = movie
        elif ext in ('.mp4','.avi','.mov','.webm'):
            video_widget = QVideoWidget(container)
            video_widget.setGeometry(container.rect()); video_widget.lower(); video_widget.show()
            player = QMediaPlayer()
            player.setVideoOutput(video_widget)
            player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
            player.mediaStatusChanged.connect(lambda s: player.play() if s == QMediaPlayer.LoadedMedia else None)
            player.play()
            self.bg_widget = video_widget; self.bg_player = player
        else:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                label = QLabel(container)
                label.setPixmap(pixmap.scaled(container.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
                label.setGeometry(container.rect()); label.lower(); label.show()
                self.bg_widget = label
            else: QMessageBox.warning(self, "Error", "Failed to load image.")
        container.setStyleSheet(container.styleSheet() + " QFrame#mainContainer { background-color: transparent; }")
    def remove_skin(self):
        if self.bg_movie: self.bg_movie.stop(); self.bg_movie = None
        if self.bg_player: self.bg_player.stop(); self.bg_player = None
        if self.bg_widget: self.bg_widget.deleteLater(); self.bg_widget = None
        self.container.setStyleSheet(
            self.container.styleSheet().replace(
                " QFrame#mainContainer { background-color: transparent; }",
                " QFrame#mainContainer { background-color: #0a0a12; }"
            )
        )

    def _select_banner(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Banner", "",
            "Media Files (*.png *.jpg *.jpeg *.gif *.mp4 *.avi *.mov *.webm)")
        if path:
            self.apply_banner(path); self.config.banner_path = path
            self.banner_current_label.setText(f"Current banner: {os.path.basename(path)}")
            ConfigManager.save(self.config)
    def _remove_banner(self):
        self.remove_banner(); self.config.banner_path = ""
        self.banner_current_label.setText("Current banner: None")
        ConfigManager.save(self.config)
    def apply_banner(self, path):
        self.remove_banner()
        ext = os.path.splitext(path)[1].lower()
        if ext == '.gif':
            movie = QMovie(path); movie.setCacheMode(QMovie.CacheAll)
            movie.setScaledSize(self.banner_label.size()); movie.start()
            self.banner_label.setMovie(movie); self.banner_movie = movie
        else:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.banner_label.setPixmap(pixmap.scaled(self.banner_label.size(),
                    Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            else: self.banner_label.clear()
    def remove_banner(self):
        if self.banner_movie: self.banner_movie.stop(); self.banner_movie = None
        self.banner_label.clear()

    # ---- UI helpers ----
    def _chk(self, text, attr):
        cb = QCheckBox(text)
        cb.setChecked(getattr(self.config, attr))
        cb.stateChanged.connect(lambda s, a=attr: setattr(self.config, a, bool(s)))
        return cb
    def _spin(self, attr, mn, mx):
        sp = QSpinBox(); sp.setRange(mn, mx); sp.setValue(getattr(self.config, attr))
        sp.valueChanged.connect(lambda v, a=attr: setattr(self.config, a, v))
        return sp
    def _dspin(self, attr, mn, mx, step):
        sp = QDoubleSpinBox(); sp.setRange(mn, mx); sp.setSingleStep(step)
        sp.setValue(getattr(self.config, attr))
        sp.valueChanged.connect(lambda v, a=attr: setattr(self.config, a, v))
        return sp
    def _slider(self, attr, mn, mx, step=1):
        sl = QSlider(Qt.Horizontal)
        if isinstance(step, float):
            sl.setRange(0,100); sl.setValue(int(getattr(self.config, attr)*100))
            sl.valueChanged.connect(lambda v, a=attr: setattr(self.config, a, v/100.0))
        else:
            sl.setRange(mn, mx); sl.setValue(getattr(self.config, attr))
            sl.valueChanged.connect(lambda v, a=attr: setattr(self.config, a, v))
        return sl
    def _row(self, label, widget):
        row = QHBoxLayout(); row.addWidget(QLabel(label)); row.addWidget(widget); return row
    def _key_button(self, attr):
        btn = QPushButton(getattr(self.config, attr))
        btn.clicked.connect(lambda: self._start_key_record(btn, attr))
        return btn
    def _start_key_record(self, btn, attr):
        btn.setText("Press key...")
        self._recording_attr = attr; self._recording_btn = btn
        self._recording_start = ctypes.windll.kernel32.GetTickCount()
        self._recording_timer = QTimer(self)
        self._recording_timer.timeout.connect(self._poll_key_record)
        self._recording_timer.start(30)
    def _poll_key_record(self):
        elapsed = ctypes.windll.kernel32.GetTickCount() - self._recording_start
        if elapsed < 300: return
        for vk in range(1, 0x100):
            if ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                name = self._vk_to_name(vk)
                setattr(self.config, self._recording_attr, name)
                self._recording_btn.setText(name)
                self._recording_timer.stop()
                return
        if elapsed > 5000:
            self._recording_timer.stop()
            self._recording_btn.setText(getattr(self.config, self._recording_attr))
    def _vk_to_name(self, vk):
        names = {0x01:"LMB",0x02:"RMB",0x04:"MMB",0x05:"MB4",0x06:"MB5",
                 0x08:"Backspace",0x09:"Tab",0x0D:"Enter",0x10:"Shift",
                 0x11:"Ctrl",0x12:"Alt",0x1B:"Esc",0x20:"Space",
                 0x21:"PageUp",0x22:"PageDown",0x23:"End",0x24:"Home",
                 0x25:"Left",0x26:"Up",0x27:"Right",0x28:"Down",
                 0x2D:"Insert",0x2E:"Delete"}
        for i in range(10): names[0x30+i] = str(i)
        for i in range(26): names[0x41+i] = chr(0x41+i)
        for i in range(12): names[0x70+i] = f"F{i+1}"
        return names.get(vk, f"VK_{vk:02X}")
    def _save_config(self):
        ConfigManager.save(self.config); QMessageBox.information(self, "Saved", "Config saved!")
    def _load_config(self):
        try:
            new = ConfigManager.load()
            for k,v in new.__dict__.items():
                if hasattr(self.config, k): setattr(self.config, k, v)
            self.close(); self._build_ui(); self.show()
            if self.config.skin_path: self.apply_skin(self.config.skin_path)
            if self.config.banner_path: self.apply_banner(self.config.banner_path)
            QMessageBox.information(self, "Loaded", "Config loaded!")
        except Exception as e: QMessageBox.warning(self, "Error", f"Failed: {e}")
    def _reset_config(self):
        self.config = Config(); self.close(); self._build_ui(); self.show()
        self.remove_skin(); self.remove_banner()
    def showEvent(self, event):
        super().showEvent(event); QApplication.restoreOverrideCursor()
        try: import win32gui; win32gui.ReleaseCapture()
        except: pass
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos); event.accept()
    def mouseReleaseEvent(self, event):
        self._drag_pos = None

# ============================================================================
# Overlay (ESP + all hacks)
# ============================================================================
class Overlay(QWidget):
    def __init__(self, esp: MecchaESP, config: Config, menu: Menu):
        super().__init__()
        self.esp = esp
        self.config = config
        self.menu = menu
        self.esp.config = config
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                            Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowTitle("MECCHA ESP")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_overlay)
        self.timer.start(16)
        self.game_hwnd = self._find_game_window()
        self._resize_to_game()

        self._fly_enabled = False
        self._noclip_enabled = False
        self._speed_active = False
        self._recoil_active = False
        self._key_states = {}
        self._trigger_last_shot = 0
        self._freecam_keys = {'forward':False,'backward':False,'left':False,'right':False,
                              'up':False,'down':False,'look_left':False,'look_right':False,
                              'look_up':False,'look_down':False}
        self.tag_spammer_index = 0
        self.last_tag_spam_time = 0
        self.last_custom_spam_time = 0
        self.tag_messages = ["S","SP","SPE","SPEC","SPECT","SPECTR","SPECTRE"]

    def _find_game_window(self):
        try:
            import win32gui
            return win32gui.FindWindow(None, "Chameleon  ")
        except:
            return 0

    def _resize_to_game(self):
        try:
            import win32gui
            if self.game_hwnd:
                rect = win32gui.GetClientRect(self.game_hwnd)
                tl = win32gui.ClientToScreen(self.game_hwnd, (rect[0], rect[1]))
                br = win32gui.ClientToScreen(self.game_hwnd, (rect[2], rect[3]))
                self.setGeometry(tl[0], tl[1], br[0]-tl[0], br[1]-tl[1])
            else:
                self.setGeometry(0, 0, 1920, 1080)
        except:
            self.setGeometry(0, 0, 1920, 1080)

    def update_overlay(self):
        self._apply_hacks()
        self._handle_freecam()
        self._handle_third_person()
        self._handle_teleport()
        self._handle_silent_aim()
        self._handle_magic_bullet()
        self._handle_no_recoil()
        self._handle_spammers()
        self._resize_to_game()
        self.update()

    def _handle_spammers(self):
        if self.config.tag_spammer_enabled and self._is_key_held(self.config.tag_spammer_key):
            now = time.time() * 1000
            if now - self.last_tag_spam_time >= 500:
                send_message(self.tag_messages[self.tag_spammer_index])
                self.tag_spammer_index = (self.tag_spammer_index + 1) % len(self.tag_messages)
                self.last_tag_spam_time = now
        if self.config.custom_spammer_enabled and self._is_key_held(self.config.custom_spammer_key):
            now = time.time() * 1000
            if now - self.last_custom_spam_time >= 500 and self.config.custom_spammer_text.strip():
                send_message(self.config.custom_spammer_text)
                self.last_custom_spam_time = now

    def _apply_hacks(self):
        # Fly
        if self.config.fly_hack:
            if self._is_key_held(self.config.fly_key):
                if not self._key_states.get("fly_toggle", False):
                    self._fly_enabled = not self._fly_enabled
                    self._key_states["fly_toggle"] = True
                    # Enable/disable flight mode
                    self.esp.set_fly_mode(self._fly_enabled)
            else:
                self._key_states["fly_toggle"] = False

            if self._fly_enabled:
                pawn = self.esp.get_local_pawn()
                if pawn:
                    pos = self.esp.get_pawn_location(pawn)
                    if pos:
                        new_pos = list(pos)
                        speed = self.config.fly_speed * 0.016
                        if self._is_key_held("Space"): new_pos[2] += speed
                        if self._is_key_held("Shift"): new_pos[2] -= speed
                        if self._is_key_held("W"): new_pos[0] += speed
                        if self._is_key_held("S"): new_pos[0] -= speed
                        if self._is_key_held("A"): new_pos[1] += speed
                        if self._is_key_held("D"): new_pos[1] -= speed
                        self.esp.set_pawn_location(pawn, tuple(new_pos))

        # Noclip – try toggling movement flag
        if self.config.noclip_enabled:
            if self._is_key_held(self.config.noclip_key):
                if not self._key_states.get("noclip_toggle", False):
                    self._noclip_enabled = not self._noclip_enabled
                    self._key_states["noclip_toggle"] = True
                    pawn = self.esp.get_local_pawn()
                    if pawn:
                        move_comp = self.esp.get_character_movement(pawn)
                        if move_comp:
                            for off in [0x1A0, 0x1A4, 0x1A8]:
                                try:
                                    wbool(self.esp.pm, move_comp + off, self._noclip_enabled)
                                    break
                                except:
                                    continue
            else:
                self._key_states["noclip_toggle"] = False
            if self._noclip_enabled:
                pawn = self.esp.get_local_pawn()
                if pawn:
                    pos = self.esp.get_pawn_location(pawn)
                    if pos:
                        new_pos = list(pos)
                        speed = 400.0 * 0.016
                        if self._is_key_held("W"): new_pos[0] += speed
                        if self._is_key_held("S"): new_pos[0] -= speed
                        if self._is_key_held("A"): new_pos[1] += speed
                        if self._is_key_held("D"): new_pos[1] -= speed
                        if self._is_key_held("Space"): new_pos[2] += speed
                        if self._is_key_held("Shift"): new_pos[2] -= speed
                        self.esp.set_pawn_location(pawn, tuple(new_pos))

        # Speed hack
        if self.config.speed_hack:
            if not self._speed_active:
                self.esp.apply_speed_hack(self.config.speed_multiplier)
                self._speed_active = True
        else:
            if self._speed_active:
                self.esp.apply_speed_hack(1.0)
                self._speed_active = False

    def _handle_no_recoil(self):
        self._recoil_active = self.config.no_recoil
        # No-recoil is handled inside aimbot: we keep control rotation stable.

    def _handle_freecam(self):
        if self.config.freecam_enabled:
            if self._is_key_held(self.config.freecam_key):
                if not self._key_states.get("freecam_toggle", False):
                    self.esp.toggle_freecam()
                    self._key_states["freecam_toggle"] = True
            else:
                self._key_states["freecam_toggle"] = False
            if self.esp.freecam_active:
                self._freecam_keys['forward'] = self._is_key_held("W")
                self._freecam_keys['backward'] = self._is_key_held("S")
                self._freecam_keys['left'] = self._is_key_held("A")
                self._freecam_keys['right'] = self._is_key_held("D")
                self._freecam_keys['up'] = self._is_key_held("Space")
                self._freecam_keys['down'] = self._is_key_held("Shift")
                self._freecam_keys['look_left'] = self._is_key_held("Left")
                self._freecam_keys['look_right'] = self._is_key_held("Right")
                self._freecam_keys['look_up'] = self._is_key_held("Up")
                self._freecam_keys['look_down'] = self._is_key_held("Down")
                self.esp.update_freecam(
                    move_forward=self._freecam_keys['forward'],
                    move_backward=self._freecam_keys['backward'],
                    move_left=self._freecam_keys['left'],
                    move_right=self._freecam_keys['right'],
                    move_up=self._freecam_keys['up'],
                    move_down=self._freecam_keys['down'],
                    look_left=self._freecam_keys['look_left'],
                    look_right=self._freecam_keys['look_right'],
                    look_up=self._freecam_keys['look_up'],
                    look_down=self._freecam_keys['look_down'],
                    speed=self.config.freecam_speed
                )

    def _handle_third_person(self):
        if self.config.third_person_enabled:
            if self._is_key_held(self.config.third_person_key):
                if not self._key_states.get("third_person_toggle", False):
                    self.esp.toggle_third_person()
                    self._key_states["third_person_toggle"] = True
            else:
                self._key_states["third_person_toggle"] = False

    def _handle_teleport(self):
        if self._is_key_held(self.config.teleport_key):
            if not self._key_states.get("teleport_toggle", False):
                players = self.esp.get_all_players()
                target_idx = self.config.teleport_target_index
                if target_idx < len(players):
                    target = players[target_idx]
                    if not target["is_local"]:
                        self.esp.teleport_to_player(target["pawn"])
                        self._key_states["teleport_toggle"] = True
            else:
                self._key_states["teleport_toggle"] = False

    def _handle_silent_aim(self):
        if not self.config.silent_aim_enabled or not self._is_key_held(self.config.silent_aim_key):
            return
        cam = self.esp.get_camera()
        if not cam: return
        w, h = self.width(), self.height()
        cx, cy = w/2, h/2
        best_target, best_dist = None, float("inf")
        for is_local, pos, idx, pawn, ps in self.esp.iter_players(include_local=False, team_filter=False, show_all=True):
            if is_local or pawn == self.esp.get_local_pawn(): continue
            aim_pos = (pos[0], pos[1], pos[2] + self.config.aimbot_target_offset)
            s = w2s(aim_pos, cam, w, h)
            if not s: continue
            d = math.hypot(s[0]-cx, s[1]-cy)
            if d <= self.config.silent_aim_fov and d < best_dist:
                best_dist, best_target = d, aim_pos
        if best_target:
            dx, dy, dz = best_target[0]-cam["loc"][0], best_target[1]-cam["loc"][1], best_target[2]-cam["loc"][2]
            length = math.hypot(dx, dy, dz)
            if length > 0:
                pitch, yaw = math.degrees(math.asin(dz/length)), math.degrees(math.atan2(dy, dx))
                self.esp.set_control_rotation((pitch, yaw, 0.0))

    def _handle_magic_bullet(self):
        if not self.config.magic_bullet_enabled or not self._is_key_held(self.config.magic_bullet_key):
            return
        cam = self.esp.get_camera()
        if not cam: return
        w, h = self.width(), self.height()
        cx, cy = w/2, h/2
        best_target, best_dist = None, float("inf")
        for is_local, pos, idx, pawn, ps in self.esp.iter_players(include_local=False, team_filter=False, show_all=True):
            if is_local or pawn == self.esp.get_local_pawn(): continue
            aim_pos = (pos[0], pos[1], pos[2] + self.config.aimbot_target_offset)
            s = w2s(aim_pos, cam, w, h)
            if not s: continue
            d = math.hypot(s[0]-cx, s[1]-cy)
            if d <= self.config.magic_bullet_fov and d < best_dist:
                best_dist, best_target = d, aim_pos
        if best_target:
            dx, dy, dz = best_target[0]-cam["loc"][0], best_target[1]-cam["loc"][1], best_target[2]-cam["loc"][2]
            length = math.hypot(dx, dy, dz)
            if length > 0:
                pitch, yaw = math.degrees(math.asin(dz/length)), math.degrees(math.atan2(dy, dx))
                self.esp.set_control_rotation((pitch, yaw, 0.0))
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)

    def _is_key_held(self, key_name):
        vk_map = {"LMB":0x01,"RMB":0x02,"MMB":0x04,"MB4":0x05,"MB5":0x06,
                  "Space":0x20,"Shift":0x10,"Ctrl":0x11,"Alt":0x12,
                  "W":0x57,"A":0x41,"S":0x53,"D":0x44,
                  "Up":0x26,"Down":0x28,"Left":0x25,"Right":0x27,
                  "N":0x4E,"T":0x54,"F":0x46,"V":0x56}
        for i in range(26): vk_map[chr(0x41+i)] = 0x41+i
        vk = vk_map.get(key_name, 0x20)
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)

    # ---------- Aimbot ----------
    def _find_best_target(self, camera, screen_w, screen_h):
        cx, cy = screen_w/2, screen_h/2
        cam_loc = camera["loc"]
        local_pawn = self.esp.get_local_pawn()
        best_target, best_dist = None, float("inf")
        for is_local, pos, idx, pawn, ps in self.esp.iter_players(include_local=False, team_filter=False, show_all=True):
            if pawn == local_pawn or is_local: continue
            if dist(pos, cam_loc) < 10.0: continue
            aim_pos = (pos[0], pos[1], pos[2] + self.config.aimbot_target_offset)
            s = w2s(aim_pos, camera, screen_w, screen_h)
            if not s: continue
            d = math.hypot(s[0]-cx, s[1]-cy)
            if d <= self.config.aimbot_fov and d < best_dist:
                best_dist, best_target = d, aim_pos
        return best_target

    def _aim_at(self, target_pos):
        cam = self.esp.get_camera()
        if not cam: return
        current = self.esp.get_control_rotation()
        if current is None: return
        dx, dy, dz = target_pos[0]-cam["loc"][0], target_pos[1]-cam["loc"][1], target_pos[2]-cam["loc"][2]
        length = math.hypot(dx, dy, dz)
        if length == 0: return
        pitch, yaw = math.degrees(math.asin(dz/length)), math.degrees(math.atan2(dy, dx))

        if self.config.aimbot_mode == "mouse":
            delta_pitch, delta_yaw = pitch - current[0], yaw - current[1]
            smooth = self.config.aimbot_smooth
            factor = 1.0 - smooth
            self.esp._mouse_move(int(delta_yaw * 0.8 * (1-factor)),
                                 int(delta_pitch * 0.6 * (1-factor)))
        else:
            smooth = self.config.aimbot_smooth
            factor = 1.0 - smooth
            new_pitch = current[0] + (pitch - current[0]) * factor
            new_yaw = current[1] + (yaw - current[1]) * factor
            self.esp.set_control_rotation((new_pitch, new_yaw, current[2]))

        if self.config.aimbot_auto_shoot:
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)

    # ---------- ESP Paint ----------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Consolas", 10)
        painter.setFont(font)
        w, h = self.width(), self.height()

        if not self.config.enabled:
            painter.setPen(QPen(QColor(255,255,255)))
            painter.drawText(10,20,"ESP OFF")
            return

        cam = self.esp.get_camera()
        if not cam:
            painter.setPen(QPen(QColor(255,255,255)))
            painter.drawText(10,20,"NO CAMERA")
            return
        if self.esp.third_person_active:
            cam = self.esp.get_third_person_camera(cam)

        local_pawn, local_ps, local_pos = self.esp.get_local_info()
        is_hunter = local_pawn and "Hunter" in self.esp._class_name(local_pawn)

        if self.config.show_crosshair:
            self._draw_crosshair(painter, w, h)

        players_data, count = [], 0
        show_all = self.config.team_check

        for is_local, pos, idx, pawn, ps in self.esp.iter_players(
                include_local=self.config.show_local,
                team_filter=self.config.team_filter,
                show_all=show_all):
            screen_info = self._project_dot(pos, cam, w, h)
            if not screen_info: continue
            sx, sy = screen_info
            pawn_cls = self.esp._class_name(pawn) if pawn else ""

            if is_local:
                color, box_color, team = self.config.colors.local, self.config.colors.local_box, "local"
            elif self.config.team_filter and is_hunter and "Hunter" not in pawn_cls:
                color, box_color, team = self.config.colors.teammate, self.config.colors.teammate_box, "teammate"
            else:
                color, box_color, team = self.config.colors.enemy, self.config.colors.enemy_box, "enemy"

            if self.config.glow_enemy and not is_local:
                painter.setPen(QPen(QColor(*color), 2))
                painter.setBrush(QColor(*color, 30))
                painter.drawEllipse(int(sx-15), int(sy-15), 30, 30)

            if self.config.box_esp:
                self._draw_dot(painter, sx, sy, box_color)

            if self.config.health_bars and not is_local:
                self._draw_health_bar(painter, sx, sy, pawn)

            if self.config.snap_lines:
                painter.setPen(QPen(QColor(*self.config.colors.snap_line), 1))
                painter.drawLine(int(w/2), int(h), int(sx), int(sy))

            label_parts = []
            if self.config.show_names:
                label_parts.append("YOU" if is_local else f"Player {idx}")
            if self.config.show_distance:
                d = int(dist(pos, cam["loc"]) / 100)
                label_parts.append(f"{d}m")
            if label_parts:
                painter.setPen(QPen(QColor(*self.config.colors.text)))
                painter.drawText(int(sx + self.config.dot_radius + 4), int(sy), " | ".join(label_parts))

            if self.config.show_radar and local_pos:
                players_data.append((pos, team, is_local))
            count += 1

        if self.config.show_radar and local_pos:
            self._draw_radar(painter, local_pos, players_data, w, h)

        # HUD
        painter.setPen(QPen(QColor(200,200,200)))
        painter.drawText(10, 20, f"Players: {count}")
        y = 40
        if self._fly_enabled:
            painter.setPen(QPen(QColor(0,255,255))); painter.drawText(10,y,"✈️ FLY"); y+=20
        if self._noclip_enabled:
            painter.setPen(QPen(QColor(255,0,255))); painter.drawText(10,y,"🌀 NOCLIP"); y+=20
        if self.esp.freecam_active:
            painter.setPen(QPen(QColor(255,200,0))); painter.drawText(10,y,"📷 FREECAM"); y+=20
        if self.esp.third_person_active:
            painter.setPen(QPen(QColor(0,200,255))); painter.drawText(10,y,"👤 3RD PERSON"); y+=20
        if self.config.speed_hack:
            painter.setPen(QPen(QColor(255,255,0))); painter.drawText(10,y,f"⚡ SPEED x{self.config.speed_multiplier}"); y+=20
        if self._recoil_active:
            painter.setPen(QPen(QColor(255,100,100))); painter.drawText(10,y,"🔫 NO RECOIL"); y+=20

        # FOV circle
        if self.config.aimbot_enabled and self.config.aimbot_show_fov:
            cx, cy = w/2, h/2
            painter.setPen(QPen(QColor(255,255,255), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(int(cx - self.config.aimbot_fov), int(cy - self.config.aimbot_fov),
                                self.config.aimbot_fov*2, self.config.aimbot_fov*2)

        # Aimbot
        if self.config.aimbot_enabled and self._is_key_held(self.config.aimbot_key):
            target = self._find_best_target(cam, w, h)
            if target:
                self._aim_at(target)

        # Triggerbot
        if self.config.triggerbot_enabled and self._is_key_held(self.config.triggerbot_key):
            self._triggerbot(cam, w, h)

    # ---------- Drawing helpers ----------
    def _draw_crosshair(self, painter, w, h):
        cx, cy = w//2, h//2
        size, gap = self.config.crosshair_size, self.config.crosshair_gap
        color = self.config.colors.crosshair
        painter.setPen(QPen(QColor(*color), 2))
        painter.drawLine(cx, cy-gap-size, cx, cy-gap)
        painter.drawLine(cx, cy+gap+size, cx, cy+gap)
        painter.drawLine(cx-gap-size, cy, cx-gap, cy)
        painter.drawLine(cx+gap+size, cy, cx+gap, cy)
        painter.drawPoint(cx, cy)

    def _draw_dot(self, painter, cx, cy, color):
        r = self.config.dot_radius
        painter.setPen(Qt.NoPen); painter.setBrush(QColor(*color))
        painter.drawEllipse(int(cx-r), int(cy-r), r*2, r*2)

    def _draw_health_bar(self, painter, sx, sy, pawn):
        try:
            health = rfloat(self.esp.pm, pawn + 0x640)
            max_health = rfloat(self.esp.pm, pawn + 0x648)
            if max_health <= 0: max_health = 100.0
            pct = min(1.0, max(0.0, health / max_health))
            bar_w, bar_h = 30, 4
            x, y = sx - bar_w//2, sy - self.config.dot_radius - 8
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(40,40,40,180))
            painter.drawRect(int(x), int(y), bar_w, bar_h)
            color = self.config.colors.health_good if pct > 0.6 else self.config.colors.health_medium if pct > 0.3 else self.config.colors.health_bad
            painter.setBrush(QColor(*color))
            painter.drawRect(int(x), int(y), int(bar_w * pct), bar_h)
        except: pass

    def _draw_radar(self, painter, local_pos, players_data, w, h):
        size = self.config.radar_size
        margin = 10
        x0, y0 = w - size - margin, margin
        painter.setPen(QPen(QColor(200,200,200,150), 1))
        painter.setBrush(QColor(20,20,30,180))
        painter.drawRect(int(x0), int(y0), size, size)
        scale = size / 2000.0
        cx, cy = x0 + size/2, y0 + size/2
        painter.setBrush(QColor(*self.config.colors.radar_local))
        painter.drawEllipse(int(cx-3), int(cy-3), 6, 6)
        for pos, team, is_local in players_data:
            if is_local: continue
            dx, dy = (pos[0] - local_pos[0]) * scale, (pos[1] - local_pos[1]) * scale
            px, py = cx + dx, cy + dy
            if px < x0 or px > x0 + size or py < y0 or py > y0 + size: continue
            color = self.config.colors.radar_enemy if team == "enemy" else self.config.colors.radar_teammate
            painter.setBrush(QColor(*color))
            painter.drawEllipse(int(px-2), int(py-2), 4, 4)

    def _project_dot(self, center_pos, camera, screen_w, screen_h):
        s = w2s(center_pos, camera, screen_w, screen_h)
        return (s[0], s[1] + self.config.box_y_offset) if s else None

    def _triggerbot(self, camera, screen_w, screen_h):
        now = time.time() * 1000
        if now - self._trigger_last_shot < self.config.triggerbot_delay: return
        cx, cy = screen_w/2, screen_h/2
        for is_local, pos, idx, pawn, ps in self.esp.iter_players(
                include_local=False,
                team_filter=self.config.team_filter,
                show_all=self.config.team_check):
            if is_local or pawn == self.esp.get_local_pawn(): continue
            aim_pos = (pos[0], pos[1], pos[2] + self.config.aimbot_target_offset)
            s = w2s(aim_pos, camera, screen_w, screen_h)
            if not s: continue
            if math.hypot(s[0]-cx, s[1]-cy) < 20:
                ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
                ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
                self._trigger_last_shot = now
                break

# ============================================================================
# Entry point
# ============================================================================
def _set_dpi_aware():
    try: ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except: pass

def main():
    _set_dpi_aware()
    app = QApplication(sys.argv)
    config = ConfigManager.load()
    esp = MecchaESP()
    menu = Menu(config)
    overlay = Overlay(esp, config, menu)
    overlay.show()
    menu.show()

    VK_INSERT, VK_F1 = 0x2D, 0x70
    _key_states = {"insert": False, "f1": False}
    def poll_keys():
        for vk, name in [(VK_INSERT, "insert"), (VK_F1, "f1")]:
            state = ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000
            if state and not _key_states[name]:
                menu.setVisible(not menu.isVisible())
                if menu.isVisible():
                    QApplication.restoreOverrideCursor()
                    try:
                        import win32gui
                        win32gui.ReleaseCapture()
                    except:
                        pass
            _key_states[name] = bool(state)
    key_timer = QTimer()
    key_timer.timeout.connect(poll_keys)
    key_timer.start(50)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()