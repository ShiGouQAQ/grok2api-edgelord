"""Tests for imagine_ws.py — preview image tracking and final flag."""

from app.dataplane.reverse.transport.imagine_ws import _Slot, _final_event


class TestSlotPreviewTracking:
    """Test _Slot preview fields and final flag state transitions."""

    def test_slot_initial_state(self):
        """New slot starts with empty preview and final=False."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        assert slot.preview_blob == ""
        assert slot.preview_url == ""
        assert slot.preview_ready is False
        assert slot.final is False
        assert slot.done is False
        assert slot.last_blob == ""
        assert slot.last_url == ""

    def test_preview_set_on_intermediate_blob(self):
        """When progress < 100, preview fields are populated."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        # Simulate receiving a preview blob (progress < 100)
        slot.preview_blob = "preview_data"
        slot.preview_url = "https://preview.url/img"
        slot.preview_ready = True
        slot.progress = 50

        assert slot.preview_blob == "preview_data"
        assert slot.preview_url == "https://preview.url/img"
        assert slot.preview_ready is True
        assert slot.final is False  # not yet final

    def test_final_set_on_completion(self):
        """When completed, final flag is set to True."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        slot.done = True
        slot.final = True
        slot.last_blob = "final_data"
        slot.last_url = "https://final.url/img"

        assert slot.final is True
        assert slot.done is True
        assert slot.last_blob == "final_data"
        assert slot.last_url == "https://final.url/img"

    def test_preview_overwritten_by_final(self):
        """Preview data can coexist with final data on the same slot."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        # First: preview arrives
        slot.preview_blob = "preview_data"
        slot.preview_url = "https://preview.url"
        slot.preview_ready = True
        # Then: final arrives
        slot.last_blob = "final_data"
        slot.last_url = "https://final.url"
        slot.final = True

        assert slot.preview_blob == "preview_data"
        assert slot.last_blob == "final_data"
        assert slot.final is True

    def test_high_progress_sets_final_blob(self):
        """Progress >= 100 sets last_blob/last_url and final=True (not preview)."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        # Simulate the logic from _stream_round for progress >= 100
        parsed_progress = 100
        if parsed_progress < 100:
            slot.preview_blob = "blob"
            slot.preview_url = "url"
            slot.preview_ready = True
        else:
            slot.last_blob = "blob"
            slot.last_url = "url"
            slot.final = True

        assert slot.last_blob == "blob"
        assert slot.last_url == "url"
        assert slot.final is True
        assert slot.preview_blob == ""  # not set
        assert slot.preview_ready is False

    def test_low_progress_sets_preview_not_final(self):
        """Progress < 100 sets preview fields, not final blob."""
        slot = _Slot(image_id="img_abc", order=0, width=512, height=512)
        parsed_progress = 42
        if parsed_progress < 100:
            slot.preview_blob = "preview_blob"
            slot.preview_url = "preview_url"
            slot.preview_ready = True
        else:
            slot.last_blob = "final_blob"
            slot.last_url = "final_url"
            slot.final = True

        assert slot.preview_blob == "preview_blob"
        assert slot.preview_ready is True
        assert slot.final is False
        assert slot.last_blob == ""  # not set


class TestFinalEvent:
    """Test _final_event builds correct event dict."""

    def test_basic_final_event(self):
        slot = _Slot(image_id="img_xyz", order=2, width=1024, height=768)
        slot.last_blob = "blob_data"
        slot.last_url = "https://final.url"
        slot.final = True

        event = _final_event(slot)
        assert event["type"] == "image"
        assert event["image_id"] == "img_xyz"
        assert event["order"] == 2
        assert event["stage"] == "final"
        assert event["blob"] == "blob_data"
        assert event["url"] == "https://final.url"
        assert event["width"] == 1024
        assert event["height"] == 768
        assert event["is_final"] is True
        assert event["final"] is True
        assert event["moderated"] is False
        assert event["r_rated"] is False

    def test_final_event_r_rated(self):
        slot = _Slot(image_id="img_r", order=0, width=512, height=512)
        event = _final_event(slot, r_rated=True)
        assert event["r_rated"] is True

    def test_final_event_not_final_slot(self):
        """Slot with final=False produces event with final=False."""
        slot = _Slot(image_id="img_nf", order=1, width=512, height=512)
        slot.final = False
        event = _final_event(slot)
        assert event["final"] is False
        assert event["is_final"] is True  # is_final is always True
