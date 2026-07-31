import json
import math
import re
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCENE_PATH = ROOT / "ui" / "office_scene.json"
SCENE = json.loads(SCENE_PATH.read_text(encoding="utf-8"))
RECT_KEYS = {"x", "y", "w", "h"}
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CATALOG_EXPECTED = {
    "deskset": ("furn2_desk_nochair.png", 56, "foot"),
    "sofaset": ("furn2_sofa_cream.png", 120, "foot"),
    "bookshelf": ("furn2_bookshelf_white.png", 48, "foot"),
    "copier": ("furn2_copier_modern.png", 44, "foot"),
    "file_cabinet": ("furn_file_cabinet.png", 96, "foot"),
    "kitchenette": ("furn2_kitchenette_modern.png", 88, "foot"),
    "noticeboard": ("furn_noticeboard.png", 72, "wall"),
    "plant_large": ("furn2_plant_modern.png", 32, "foot"),
    "planterbox": ("furn2_planterbox_modern.png", 110, "foot"),
    "reception": ("furn2_reception.png", 120, "foot"),
    "crt_station": ("furn2_crt_station.png", 32, "foot"),
    "meeting_table": ("furn2_meeting_table.png", 132, "foot"),
    "rug_lounge": ("furn_rug_lounge.png", 144, "under"),
    "rug_meeting": ("furn2_rug_meeting.png", 190, "under"),
    "server_led": ("furn2_server_led.png", 96, "foot"),
    "wall_clock": ("furn_wall_clock.png", 24, "wall"),
    "whiteboard": ("furn2_whiteboard_modern.png", 84, "foot"),
    "window_wide": ("furn_window_wide.png", 120, "wall"),
    "exec_planter": ("furn2_exec_planter.png", 26, "foot"),
    "exec_sofa": ("furn2_exec_sofa.png", 48, "foot"),
    "exec_table": ("furn2_exec_table.png", 44, "foot"),
    "exec_desk": ("furn2_exec_desk.png", 48, "foot"),
}

def rect(value, label):
    if not isinstance(value, dict):
        raise AssertionError(f"{label} must be an object")
    if not RECT_KEYS.issubset(value):
        raise AssertionError(f"{label} must include {RECT_KEYS}: {value}")
    if not all(isinstance(value[key], (int, float)) and not isinstance(value[key], bool)
               for key in RECT_KEYS):
        raise AssertionError(f"{label} has non-numeric coordinates: {value}")
    return value


def inside(container, item):
    return (item["x"] >= container["x"] and item["y"] >= container["y"] and
            item["x"] + item["w"] <= container["x"] + container["w"] and
            item["y"] + item["h"] <= container["y"] + container["h"])


def intersects(left, right):
    return (left["x"] < right["x"] + right["w"] and
            left["x"] + left["w"] > right["x"] and
            left["y"] < right["y"] + right["h"] and
            left["y"] + left["h"] > right["y"])


def active_furniture(mode):
    return [item for item in SCENE["furniture"] if mode in item.get("modes", [])]


def js_round(value):
    return math.floor(value + 0.5)


class SceneGeometryTests(unittest.TestCase):
    def test_schema_and_references(self):
        self.assertEqual(SCENE["version"], 1)
        self.assertEqual(SCENE["defaultMode"], "tiles")
        self.assertEqual(SCENE["logical"], {"w": 1080, "h": 720})
        self.assertEqual(SCENE["tile"], 24)
        for key in ("rooms", "doors", "walls", "furniture", "deskAnchors", "defaultLayout", "catalog"):
            self.assertIn(key, SCENE)

        rooms = SCENE["rooms"]
        self.assertIsInstance(rooms, list)
        room_ids = [room["id"] for room in rooms]
        self.assertEqual(len(room_ids), len(set(room_ids)))
        room_map = {}
        for room in rooms:
            for key in ("id", "kind", "label", "desks", "floorTile", "floorColor",
                        "floor", "standby", "door", "doorId"):
                self.assertIn(key, room)
            self.assertIsInstance(room["id"], str)
            self.assertIsInstance(room["kind"], str)
            self.assertIsInstance(room["label"], str)
            self.assertIsInstance(room["desks"], int)
            self.assertIsInstance(room["floorTile"], str)
            self.assertRegex(room["floorColor"], HEX_COLOR_RE)
            rect(room["floor"], f"room {room['id']} floor")
            rect(room["door"], f"room {room['id']} door")
            self.assertIsInstance(room["standby"], list)
            for point in room["standby"]:
                self.assertIsInstance(point, list)
                self.assertEqual(len(point), 2)
                self.assertTrue(all(isinstance(value, (int, float)) for value in point))
            room_map[room["id"]] = room

        doors = SCENE["doors"]
        door_ids = [door["id"] for door in doors]
        self.assertEqual(len(door_ids), len(set(door_ids)))
        door_map = {}
        for door in doors:
            self.assertIsInstance(door["id"], str)
            self.assertIn(door["axis"], ("horizontal", "vertical"))
            rect(door["rect"], f"door {door['id']} rect")
            self.assertIsInstance(door.get("rooms"), list)
            self.assertGreaterEqual(len(door["rooms"]), 1)
            for room_id in door["rooms"]:
                self.assertIn(room_id, room_map)
            door_map[door["id"]] = door
        for room in rooms:
            self.assertIn(room["doorId"], door_map)
            self.assertIn(room["id"], door_map[room["doorId"]]["rooms"])
            self.assertTrue(
                room["door"] == door_map[room["doorId"]]["rect"] or
                inside(door_map[room["doorId"]]["rect"], room["door"]),
                f"room {room['id']} door is not covered by {room['doorId']}",
            )

        furniture_ids = []
        for furniture in SCENE["furniture"]:
            for key in ("id", "type", "room", "modes", "z"):
                self.assertIn(key, furniture)
            self.assertIsInstance(furniture["id"], str)
            self.assertIsInstance(furniture["type"], str)
            self.assertIn(furniture["room"], room_map)
            self.assertEqual(furniture["modes"], ["legacy"] if furniture.get("bakedIn")
                             else ["tiles"])
            self.assertIn(furniture["z"], ("under", "wall", "foot"))
            if furniture["z"] == "foot":
                rect(furniture["footprint"], f"furniture {furniture['id']} footprint")
            else:
                self.assertNotIn("footprint", furniture)
            furniture_ids.append(furniture["id"])
        self.assertEqual(len(furniture_ids), len(set(furniture_ids)))

        for wall in SCENE["walls"]:
            rect(wall, "wall")

        self.assertIn("wall", SCENE)
        self.assertIsInstance(SCENE["wall"], dict)
        for key in ("faceColor", "topColor", "faceTile", "topTile", "faceHeight"):
            self.assertIn(key, SCENE["wall"])
        self.assertRegex(SCENE["wall"]["faceColor"], HEX_COLOR_RE)
        self.assertRegex(SCENE["wall"]["topColor"], HEX_COLOR_RE)
        self.assertIsInstance(SCENE["wall"]["faceTile"], str)
        self.assertIsInstance(SCENE["wall"]["topTile"], str)
        self.assertIsInstance(SCENE["wall"]["faceHeight"], (int, float))
        self.assertGreaterEqual(SCENE["wall"]["faceHeight"], 0)

    def test_scene_tiles_exist_as_96px_pngs(self):
        tile_slugs = [room["floorTile"] for room in SCENE["rooms"]]
        tile_slugs.extend([SCENE["wall"]["faceTile"], SCENE["wall"]["topTile"]])
        tile_slugs.extend(wall["faceTile"] for wall in SCENE["walls"] if wall.get("faceTile"))
        for slug in tile_slugs:
            asset = ROOT / "assets" / f"{slug}.png"
            self.assertTrue(asset.is_file(), f"scene tile missing: {asset}")
            self.assertEqual(asset.read_bytes()[:8], b"\x89PNG\r\n\x1a\n",
                             f"scene tile is not a PNG: {asset}")
            with Image.open(asset) as image:
                self.assertEqual(image.size, (96, 96), f"scene tile size: {asset}")
                image.verify()

    def test_default_mode_is_tiles_and_legacy_is_fully_retired(self):
        # legacyハッチはR30-P7で廃止（旧bg実測データと新間取りの不整合を残さない）
        tiles = active_furniture(SCENE["defaultMode"])
        self.assertEqual(len(tiles), 39)
        self.assertTrue(all(item.get("modes") == ["tiles"] for item in tiles))
        self.assertEqual(active_furniture("legacy"), [])
        self.assertFalse(any(item.get("bakedIn") for item in SCENE["furniture"]))

    def test_furniture_does_not_overlap(self):
        for mode in ("tiles",):
            furniture = [item for item in active_furniture(mode) if item["z"] == "foot"]
            for index, left in enumerate(furniture):
                for right in furniture[index + 1:]:
                    self.assertFalse(
                        intersects(left["footprint"], right["footprint"]),
                        f"{mode}家具が交差: {left['id']} / {right['id']}",
                    )

    def test_catalog_has_the_r30_p16_types_and_assets(self):
        self.assertEqual(set(CATALOG_EXPECTED), set(SCENE["catalog"]))
        for name, (filename, display_w, z) in CATALOG_EXPECTED.items():
            item = SCENE["catalog"][name]
            self.assertEqual(item["sprite"], filename)
            self.assertEqual(item["displayW"], display_w)
            self.assertEqual(item["z"], z)
            asset = ROOT / "assets" / filename
            with Image.open(asset) as image:
                image.verify()
                display_h = js_round(display_w * image.height / image.width)
            if name == "deskset":
                self.assertEqual(item["footprint"], {"dx": 0, "dy": 0, "w": 56, "h": 44})
            elif name == "sofaset":
                self.assertEqual(item["displayH"], display_h)
                self.assertEqual(item["footprint"], {"dx": 0, "dy": 56, "w": 120, "h": 24})
            elif name in {"exec_planter", "exec_table", "exec_desk"}:
                # 参考画像クロップの縦物: footprint=全身フル設計(下端10px抜き)
                self.assertEqual(item["footprint"]["dx"], 0)
                self.assertEqual(item["footprint"]["dy"], 0)
            elif z == "foot":
                self.assertIn("footprint", item)
                fp = item["footprint"]
                narrowed = name in {"plant_large", "planterbox", "server_led"}
                expected_w = js_round(display_w * (0.7 if name == "server_led" else 0.8)) if narrowed else display_w
                expected_dx = js_round((display_w - expected_w) / 2) if narrowed else 0
                self.assertEqual(fp, {
                    "dx": expected_dx,
                    "dy": js_round(display_h * 0.7),
                    "w": expected_w,
                    "h": js_round(display_h * 0.3),
                })
            else:
                self.assertNotIn("footprint", item)

    def test_tiles_furniture_is_grid_snapped_and_geometry_safe(self):
        rooms = {room["id"]: room for room in SCENE["rooms"]}
        tiles = active_furniture("tiles")
        self.assertEqual(len(tiles), 39)
        def desk_rect(anchor):
            if anchor.get("type") == "crt_station":
                fp = SCENE["catalog"]["crt_station"]["footprint"]
                return {"x": anchor["dx"] + fp["dx"], "y": anchor["dy"] + fp["dy"],
                        "w": fp["w"], "h": fp["h"]}
            return {"x": anchor["dx"], "y": anchor["dy"], "w": 56, "h": 44}
        room_desk_rects = {
            room_id: [desk_rect(a)
                      for a in SCENE["deskAnchors"] if a["region"] == room_id]
            for room_id in rooms
        }
        room_desk_rects["main"] += [
            {"x": a["dx"], "y": a["dy"], "w": 56, "h": 44}
            for a in SCENE["defaultLayout"]["desks"]
        ]
        for furniture in tiles:
            if furniture["type"] not in {"ceo_desk", "server_led", "wall_clock", "noticeboard"}:
                self.assertEqual(furniture["x"] % SCENE["tile"], 0, furniture)
            # LEDラックは北壁を3台で埋めるためxも24px格子外の列ピッチを許容する。
            if furniture["type"] == "ceo_desk":
                self.assertEqual((furniture["x"], furniture["y"]), (132, 420), furniture)
            elif furniture["type"] == "server_led":
                self.assertIn(furniture["x"], (776, 868, 960), furniture)
                self.assertEqual(furniture["y"], 32, furniture)
            elif furniture["type"] == "kitchenette":
                self.assertEqual((furniture["x"], furniture["y"]), (960, 316), furniture)
            elif furniture["type"] in ("wall_clock", "noticeboard") and furniture["z"] == "wall":
                # 仕切り壁マウント: 壁帯(288..312)センターに掛けるため格子非整合を許容
                pass
            elif furniture["type"] == "crt_station":
                # 3行が歩行帯(セル中心線+クリアランス14)を確保できる60pxピッチ=非24格子を許容
                self.assertIn(furniture["y"], (84, 144, 204), furniture)
            else:
                self.assertEqual(furniture["y"] % SCENE["tile"], 0, furniture)
            if furniture["z"] != "foot":
                continue
            room = rooms[furniture["room"]]
            footprint = furniture["footprint"]
            # R30-P16のキッチンカウンターは右壁際の表示基準点(960,320)を正本とする。
            # 画像の透明余白を含むdisplay幅が壁をまたぐため、衝突footprintのみ検査する。
            if furniture["type"] != "kitchenette":
                self.assertTrue(inside(room["floor"], footprint), furniture)
            for desk in room_desk_rects[furniture["room"]]:
                self.assertFalse(intersects(footprint, desk), furniture)
            for point in room["standby"]:
                self.assertFalse(
                    footprint["x"] <= point[0] < footprint["x"] + footprint["w"] and
                    footprint["y"] <= point[1] < footprint["y"] + footprint["h"],
                    f"standbyと家具が交差: {furniture['id']} / {point}",
                )
            # main-brk-openはbrk西側、main-ceo-openはceo東側の開放境界。
            # コピー機は仕切り帯の左壁沿いに置くため、proj1-mainドア動線との
            # 非干渉を別途検査する。
            if furniture["id"] == "main-copier":
                self.assertFalse(
                    intersects(footprint, {"x": 108, "y": 312, "w": 72, "h": 24}),
                    furniture,
                )
            elif furniture["id"] == "meet-table":
                # 開放部屋のroom.doorは境界の一部を表すメタデータであり、
                # meeting_tableはガラス個室の床内に置くため、ドア開口とは分離して検査する。
                continue
            else:
                self.assertFalse(intersects(footprint, room["door"]), furniture)

    def test_tiles_walls_and_rug_are_non_colliding(self):
        for furniture in active_furniture("tiles"):
            if furniture["z"] in ("under", "wall"):
                self.assertNotIn(
                    "footprint", furniture,
                    f"非衝突家具にfootprintがあります: {furniture['id']}",
                )

    def test_desk_anchors_and_default_layout_are_inside_floors(self):
        rooms = {room["id"]: room for room in SCENE["rooms"]}
        for anchor in SCENE["deskAnchors"]:
            self.assertIn(anchor["region"], rooms)
            self.assertTrue(
                inside(rooms[anchor["region"]]["floor"],
                       {"x": anchor["dx"], "y": anchor["dy"], "w": 56, "h": 44}),
                anchor,
            )
        for anchor in SCENE["defaultLayout"]["desks"]:
            self.assertTrue(
                inside(rooms["main"]["floor"],
                       {"x": anchor["dx"], "y": anchor["dy"], "w": 56, "h": 44}),
                anchor,
            )

    def test_r30_p16_main_geometry_and_glass_meeting_room(self):
        rooms = {room["id"]: room for room in SCENE["rooms"]}
        self.assertEqual(rooms["main"]["desks"], 8)
        self.assertEqual((rooms["main"]["floor"]["x"], rooms["main"]["floor"]["y"],
                          rooms["main"]["floor"]["w"], rooms["main"]["floor"]["h"]),
                         (24, 312, 1032, 307))
        self.assertEqual(
            {room_id: rooms[room_id]["floor"]["h"] for room_id in
             ("proj1", "proj2", "proj3", "openclaw")},
            {"proj1": 264, "proj2": 264, "proj3": 264, "openclaw": 264},
        )
        self.assertEqual(
            SCENE["defaultLayout"]["desks"],
            [{"dx": dx, "dy": dy} for dy in (408, 488) for dx in (336, 456, 576, 696)],
        )

        furniture = {item["id"]: item for item in SCENE["furniture"]}
        self.assertNotIn("main-reception", furniture)
        self.assertFalse(any(item["type"] == "reception" for item in SCENE["furniture"]))
        self.assertEqual(
            next(door for door in SCENE["doors"] if door["id"] == "main-brk-open"),
            {"id": "main-brk-open", "rect": {"x": 792, "y": 312, "w": 24, "h": 120},
             "axis": "vertical", "rooms": ["main", "brk"]},
        )
        self.assertEqual(
            next(door for door in SCENE["doors"] if door["id"] == "main-ceo-open"),
            {"id": "main-ceo-open", "rect": {"x": 288, "y": 408, "w": 24, "h": 211},
             "axis": "vertical", "rooms": ["main", "ceo"]},
        )
        ceo = furniture["ceo-desk"]
        self.assertEqual(ceo["type"], "exec_desk")
        self.assertEqual(ceo["room"], "ceo")
        self.assertEqual((ceo["x"], ceo["y"]), (192, 408))
        self.assertEqual(ceo["footprint"], {
            "x": 192 + SCENE["catalog"]["exec_desk"]["footprint"]["dx"],
            "y": 408 + SCENE["catalog"]["exec_desk"]["footprint"]["dy"],
            "w": SCENE["catalog"]["exec_desk"]["footprint"]["w"],
            "h": SCENE["catalog"]["exec_desk"]["footprint"]["h"],
        })
        # R30-P18: 司令官室=モダン参考01のエグゼクティブ列（プランター/ソファ×2/テーブル/縦デスク）
        self.assertEqual(
            {item["id"] for item in SCENE["furniture"] if item["room"] == "ceo"},
            {"ceo-planter", "ceo-sofa-north", "ceo-table", "ceo-sofa-south", "ceo-desk"},
        )
        self.assertEqual((furniture["main-kitchenette"]["x"], furniture["main-kitchenette"]["y"]),
                         (960, 316))
        self.assertEqual((furniture["main-noticeboard"]["x"], furniture["main-noticeboard"]["y"]),
                         (864, 266))
        copier = furniture["main-copier"]
        self.assertEqual((copier["x"], copier["y"]), (24, 312))
        self.assertEqual(copier["footprint"], {"x": 24, "y": 358, "w": 44, "h": 20})
        bookshelf = furniture["proj2-bookshelf"]
        self.assertEqual((bookshelf["x"], bookshelf["y"]), (288, 24))
        self.assertEqual(bookshelf["footprint"], {"x": 288, "y": 109, "w": 48, "h": 37})
        meet = rooms["meet"]
        self.assertEqual(meet["kind"], "meeting")
        self.assertFalse(meet["open"])
        self.assertEqual((meet["floor"]["x"], meet["floor"]["y"],
                          meet["floor"]["w"], meet["floor"]["h"]),
                         (816, 432, 240, 187))
        self.assertEqual(SCENE["doors"][-1], {
            "id": "main-meet-door", "rect": {"x": 792, "y": 504, "w": 24, "h": 72},
            "axis": "vertical", "rooms": ["main", "meet"],
        })
        self.assertEqual(
            [wall for wall in SCENE["walls"] if wall.get("faceTile") == "tile2_wall_glass" and
             ((wall["x"], wall["y"]) in ((792, 432), (792, 576)))],
            [
                {"x": 792, "y": 432, "w": 24, "h": 72, "faceTile": "tile2_wall_glass"},
                {"x": 792, "y": 576, "w": 24, "h": 43, "faceTile": "tile2_wall_glass"},
            ],
        )
        self.assertEqual(
            next(wall for wall in SCENE["walls"] if (wall["x"], wall["y"]) == (816, 432)),
            {"x": 816, "y": 432, "w": 240, "h": 24, "faceTile": "tile2_wall_interior"},
        )
        table = furniture["meet-table"]
        self.assertEqual((table["x"], table["y"]), (864, 504))
        self.assertEqual(table["footprint"], {"x": 864, "y": 553, "w": 132, "h": 21})

    def test_r30_p16_theme_lounge_and_openclaw_geometry(self):
        rooms = {room["id"]: room for room in SCENE["rooms"]}
        self.assertEqual(
            {room_id: rooms[room_id]["floorTile"] for room_id in rooms},
            {"main": "tile2_floor_white", "proj1": "tile2_floor_white",
             "proj2": "tile2_floor_white", "proj3": "tile2_floor_white",
             "openclaw": "tile2_floor_dark", "brk": "tile2_floor_white",
             "ceo": "tile2_floor_white",
             "meet": "tile2_floor_wood"},
        )
        self.assertEqual((rooms["brk"]["floor"]["x"], rooms["brk"]["floor"]["y"],
                          rooms["brk"]["floor"]["w"], rooms["brk"]["floor"]["h"]),
                         (816, 312, 240, 120))
        self.assertEqual((rooms["ceo"]["floor"]["x"], rooms["ceo"]["floor"]["y"],
                          rooms["ceo"]["floor"]["w"], rooms["ceo"]["floor"]["h"]),
                         (24, 408, 264, 211))
        self.assertEqual(rooms["brk"]["standby"], [[984, 352], [952, 352]])
        self.assertEqual(rooms["ceo"]["standby"], [[240, 496], [120, 548]])
        self.assertEqual(rooms["openclaw"]["desks"], 0)  # CRTは装飾家具(R7実接続で座席化)
        self.assertEqual(sum(anchor["region"] == "openclaw" for anchor in SCENE["deskAnchors"]), 0)
        self.assertEqual(sum(wall.get("faceTile") == "tile2_wall_glass" for wall in SCENE["walls"]), 8)
        self.assertNotIn("openclaw-window-wide", {item["id"] for item in SCENE["furniture"]})
        openclaw_furniture = [item for item in SCENE["furniture"] if item["room"] == "openclaw"]
        self.assertEqual({item["type"] for item in openclaw_furniture}, {"server_led", "crt_station"})
        self.assertEqual(len(openclaw_furniture), 12)
        self.assertEqual([(item["x"], item["y"]) for item in openclaw_furniture
                          if item["type"] == "server_led"],
                         [(776, 32), (868, 32), (960, 32)])
        self.assertEqual(sorted((item["x"], item["y"]) for item in openclaw_furniture
                                if item["type"] == "crt_station"),
                         sorted((x, y) for x in (792, 888, 984) for y in (84, 144, 204)))
        self.assertTrue(SCENE["catalog"]["server_led"]["glow"])
        self.assertEqual(SCENE["rooms"][[room["id"] for room in SCENE["rooms"]].index("openclaw")]["botPins"],
                         [[834, 200], [930, 200], [1008, 200]])
        meeting = {item["id"]: item for item in SCENE["furniture"] if item["room"] == "meet"}
        self.assertEqual(meeting["meet-rug"], {
            "id": "meet-rug", "type": "rug_meeting", "room": "meet", "modes": ["tiles"],
            "x": 840, "y": 480, "z": "under",
        })
        self.assertEqual((meeting["meet-whiteboard"]["x"], meeting["meet-whiteboard"]["y"]), (888, 432))
        self.assertEqual(
            {item["id"] for item in SCENE["furniture"] if item["type"] == "planterbox"},
            {"main-planterbox-left", "main-planterbox-right"},
        )

    def test_catalog_sprites_exist_and_are_readable_pngs(self):
        for name, item in SCENE["catalog"].items():
            sprite = ROOT / "assets" / item["sprite"]
            self.assertTrue(sprite.is_file(), f"catalog {name}: {sprite}")
            with Image.open(sprite) as image:
                image.verify()
            self.assertGreater(sprite.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
