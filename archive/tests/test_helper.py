"""Tests for the helper config + sprite module.

Pure validation + sprite-manifest integrity + roundtrip through user_store.
Sprites are GIF files served from sentinel-ui/public/sprites/; this module
only owns the manifest that points at them.
"""
import pytest

from sentinel.core import helper


# --- validate_patch ----------------------------------------------
class TestValidatePatch:
    def test_empty_dict_is_noop(self):
        assert helper.validate_patch({}) == {}

    def test_non_dict_is_empty(self):
        assert helper.validate_patch(None) == {}
        assert helper.validate_patch("not a dict") == {}
        assert helper.validate_patch(42) == {}

    def test_name_trimmed_and_clamped(self):
        out = helper.validate_patch({"name": "   Joby   "})
        assert out["name"] == "Joby"

    def test_name_max_length_enforced(self):
        long = "x" * (helper.MAX_NAME_LENGTH + 10)
        out = helper.validate_patch({"name": long})
        assert len(out["name"]) == helper.MAX_NAME_LENGTH

    def test_empty_name_dropped(self):
        assert "name" not in helper.validate_patch({"name": "   "})

    def test_non_string_name_dropped(self):
        assert "name" not in helper.validate_patch({"name": 42})

    def test_unknown_sprite_dropped(self):
        assert "sprite" not in helper.validate_patch({"sprite": "dragon"})

    def test_valid_sprite_kept(self):
        assert helper.validate_patch({"sprite": "rollo"}) == {"sprite": "rollo"}

    def test_unknown_eyes_dropped(self):
        assert "eyes" not in helper.validate_patch({"eyes": "glowing"})

    def test_valid_eyes_kept(self):
        assert helper.validate_patch({"eyes": "focus"}) == {"eyes": "focus"}

    def test_unknown_accessory_dropped(self):
        assert "accessory" not in helper.validate_patch({"accessory": "crown"})

    def test_valid_accessory_kept(self):
        assert helper.validate_patch({"accessory": "beanie"}) == {"accessory": "beanie"}

    def test_unknown_keys_dropped(self):
        out = helper.validate_patch({"evil_key": "x", "name": "Joby"})
        assert out == {"name": "Joby"}

    def test_mixed_valid_and_invalid(self):
        out = helper.validate_patch({
            "name": "Rollo",
            "sprite": "rollo",
            "eyes": "happy",
            "accessory": "crown",   # dropped
        })
        assert out == {"name": "Rollo", "sprite": "rollo", "eyes": "happy"}


# --- resolve -----------------------------------------------------
class TestResolve:
    def test_none_returns_default_view(self):
        v = helper.resolve(None)
        assert v.name == "Joby"
        assert v.sprite == helper.DEFAULT_SPRITE

    def test_assets_attached(self):
        v = helper.resolve({"sprite": "rollo"})
        assert v.asset_url == "/sprites/rollo_idle.gif"
        assert v.assets["idle"] == "/sprites/rollo_idle.gif"
        assert v.assets["wave"] == "/sprites/rollo_wave.gif"
        assert v.assets["bounce"] == "/sprites/rollo_bounce.gif"
        assert v.assets["celebrate"] == "/sprites/rollo_celebrate.gif"
        assert v.assets["sleep"] == "/sprites/rollo_sleep.gif"
        assert v.label == "Rollo"
        assert v.credit

    def test_asset_url_is_idle_state(self):
        v = helper.resolve({"sprite": "joby"})
        assert v.asset_url == v.assets[helper.DEFAULT_STATE]

    def test_corrupt_sprite_falls_back(self):
        v = helper.resolve({"sprite": "dragon"})
        assert v.sprite == helper.DEFAULT_HELPER["sprite"]
        default_assets = helper.SPRITES[helper.DEFAULT_SPRITE]["assets"]
        assert v.asset_url == default_assets[helper.DEFAULT_STATE]

    def test_to_dict_shape(self):
        out = helper.resolve(None).to_dict()
        assert set(out.keys()) == {
            "name", "sprite", "eyes", "accessory",
            "asset_url", "assets", "label", "credit",
        }

    def test_to_dict_assets_has_all_states(self):
        out = helper.resolve(None).to_dict()
        assert set(out["assets"].keys()) == set(helper.STATES)

    def test_custom_name_preserved(self):
        v = helper.resolve({"name": "Bonk"})
        assert v.name == "Bonk"


# --- sprite manifest integrity -----------------------------------
class TestSpriteManifest:
    def test_default_sprite_exists(self):
        assert helper.DEFAULT_SPRITE in helper.SPRITES

    def test_every_sprite_has_label_description_credit(self):
        for key, data in helper.SPRITES.items():
            assert isinstance(data.get("label"), str) and data["label"]
            assert isinstance(data.get("description"), str) and data["description"]
            assert isinstance(data.get("credit"), str) and data["credit"]

    def test_every_sprite_has_all_states(self):
        for key, data in helper.SPRITES.items():
            for state in helper.STATES:
                assert state in data["assets"], \
                    f"{key} missing state {state}"

    def test_every_asset_url_under_sprites(self):
        for key, data in helper.SPRITES.items():
            for state, url in data["assets"].items():
                assert url.startswith("/sprites/"), \
                    f"{key}:{state} url not under /sprites/"
                assert url.endswith(".gif"), \
                    f"{key}:{state} not a .gif"

    def test_asset_filenames_match_key_and_state(self):
        for key, data in helper.SPRITES.items():
            for state, url in data["assets"].items():
                assert url == f"/sprites/{key}_{state}.gif", \
                    f"{key}:{state} -> {url}"


# --- list_options ------------------------------------------------
class TestListOptions:
    def test_shape(self):
        out = helper.list_options()
        assert set(out.keys()) == {
            "sprites", "eyes", "accessories", "states", "happy_tiers",
            "burst_ms", "name_limits", "default",
        }

    def test_burst_ms_covers_burst_states(self):
        out = helper.list_options()
        for state in ("blink", "look", "nod", "shake", "think", "eat"):
            assert state in out["burst_ms"], f"{state} missing from burst_ms"
            assert out["burst_ms"][state] > 0

    def test_happy_tiers_shape(self):
        tiers = helper.list_options()["happy_tiers"]
        assert len(tiers) >= 3
        # Must be sorted ascending by min so the frontend can walk highest-first
        mins = [t["min"] for t in tiers]
        assert mins == sorted(mins)
        for t in tiers:
            assert t["state"] in helper.STATES
            assert t["mood"] in helper.SAYINGS, \
                f"tier {t['state']} points at missing mood {t['mood']}"

    def test_sprite_entries_have_assets(self):
        for entry in helper.list_options()["sprites"]:
            assert entry["key"] in helper.SPRITES
            assert entry["asset_url"].startswith("/sprites/")
            assert set(entry["assets"].keys()) == set(helper.STATES)
            assert entry["label"]
            assert entry["credit"]

    def test_states_advertised(self):
        assert helper.list_options()["states"] == list(helper.STATES)

    def test_default_is_joby(self):
        assert helper.list_options()["default"]["name"] == "Joby"

    def test_name_limits(self):
        out = helper.list_options()
        assert out["name_limits"]["min"] == helper.MIN_NAME_LENGTH
        assert out["name_limits"]["max"] == helper.MAX_NAME_LENGTH


# --- sayings -----------------------------------------------------
class TestSayings:
    def test_idle_default(self):
        out = helper.sayings()
        assert len(out) >= 10

    def test_unknown_mood_falls_back_to_idle(self):
        out = helper.sayings("not-a-mood")
        assert out == helper.sayings("idle")

    def test_each_mood_has_content(self):
        for mood in ("idle", "cycle_start", "match_found",
                     "empty_cycle", "encourage", "celebrate",
                     "pet_wave", "pet_bounce", "pet_celebrate",
                     "decision_keep", "decision_skip",
                     "cycle_working", "match_eat"):
            assert len(helper.sayings(mood)) >= 3, f"{mood} too thin"

    def test_returned_list_is_copy(self):
        out = helper.sayings("idle")
        out.clear()
        assert len(helper.sayings("idle")) > 0

    def test_no_empty_sayings(self):
        for mood, items in helper.SAYINGS.items():
            for s in items:
                assert isinstance(s, str) and s.strip(), f"{mood}: empty saying"


# --- invariants --------------------------------------------------
class TestInvariants:
    def test_default_sprite_is_in_sprites(self):
        assert helper.DEFAULT_HELPER["sprite"] in helper.SPRITES

    def test_default_eyes_is_valid(self):
        assert helper.DEFAULT_HELPER["eyes"] in helper.EYE_STYLES

    def test_default_accessory_is_valid(self):
        assert helper.DEFAULT_HELPER["accessory"] in helper.ACCESSORIES

    def test_default_helper_returns_independent_copy(self):
        a = helper.default_helper()
        a["name"] = "Mutated"
        assert helper.DEFAULT_HELPER["name"] == "Joby"


# --- integration with user_store ---------------------------------
class TestUserStoreRoundtrip:
    def test_fresh_user_store_has_joby(self, tmp_path):
        from sentinel.core import user_store
        data = user_store.load(tmp_path)
        assert data["helper"]["name"] == "Joby"
        assert data["helper"]["sprite"] == "joby"

    def test_update_helper_persists(self, tmp_path):
        view = helper.update_helper(tmp_path,
                                    {"name": "Rollo", "sprite": "rollo"})
        assert view.name == "Rollo"
        assert view.sprite == "rollo"
        assert view.asset_url == "/sprites/rollo_idle.gif"
        assert view.assets["celebrate"] == "/sprites/rollo_celebrate.gif"
        again = helper.read_helper(tmp_path)
        assert again.sprite == "rollo"

    def test_update_helper_merges(self, tmp_path):
        helper.update_helper(tmp_path, {"name": "Rollo"})
        helper.update_helper(tmp_path, {"sprite": "momo"})
        view = helper.read_helper(tmp_path)
        assert view.name == "Rollo"   # survived
        assert view.sprite == "momo"

    def test_update_helper_ignores_bad_values(self, tmp_path):
        helper.update_helper(tmp_path, {"name": "Rollo"})
        helper.update_helper(tmp_path, {"sprite": "dragon"})  # invalid
        view = helper.read_helper(tmp_path)
        assert view.name == "Rollo"
        assert view.sprite == helper.DEFAULT_HELPER["sprite"]

    def test_read_helper_on_fresh_dir(self, tmp_path):
        view = helper.read_helper(tmp_path)
        assert view.name == "Joby"
        assert view.asset_url.startswith("/sprites/")
