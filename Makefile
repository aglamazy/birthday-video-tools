PYTHON ?= python3
CONFIG ?= config.json

SEQUENCE_DIR := sequence
SEGMENTS_DIR := segments
AUDIO_DIR := audio/selected

OUTPUT_BASE := output
OUTPUT_VIDEO := $(OUTPUT_BASE).video.mp4
OUTPUT_AUDIO := $(OUTPUT_BASE).mp3
FINAL_OUTPUT := $(OUTPUT_BASE).mp4

SEQUENCE_PREFIXES := $(shell $(PYTHON) tools/list_prefixes.py --directory $(SEQUENCE_DIR))
SEGMENT_TARGETS := $(addprefix $(SEGMENTS_DIR)/,$(addsuffix .mp4,$(SEQUENCE_PREFIXES)))

AUDIO_INPUTS := $(sort $(wildcard $(AUDIO_DIR)/*))

.PHONY: all segments clean

all: $(FINAL_OUTPUT)

segments: $(SEGMENT_TARGETS)


$(SEGMENTS_DIR)/:
	@mkdir -p $@

define make_segment_rule
$(SEGMENTS_DIR)/$(1).mp4: $(wildcard $(SEQUENCE_DIR)/$(1)*) | $(SEGMENTS_DIR)/
	$(PYTHON) tools/segment_maker.py --base $(1) --config $(CONFIG) --segments-dir $(SEGMENTS_DIR) --output $$@ --subtitles-dir $(SEGMENTS_DIR)/subtitles/$(1)
endef

$(foreach base,$(SEQUENCE_PREFIXES),$(eval $(call make_segment_rule,$(base))))

$(OUTPUT_VIDEO): $(SEGMENT_TARGETS)
	$(PYTHON) tools/concat_video.py --output $@ --segments-dir $(SEGMENTS_DIR) --segments $(SEGMENT_TARGETS)

$(OUTPUT_AUDIO): $(AUDIO_INPUTS)
	$(PYTHON) tools/combine_audio.py --output $@ $(AUDIO_INPUTS)

$(FINAL_OUTPUT): $(OUTPUT_VIDEO) $(OUTPUT_AUDIO)
	$(PYTHON) tools/mux_output.py --video $(OUTPUT_VIDEO) --audio $(OUTPUT_AUDIO) --output-base $(OUTPUT_BASE)

clean:
	rm -rf $(SEGMENTS_DIR) $(OUTPUT_VIDEO) $(OUTPUT_AUDIO) $(FINAL_OUTPUT) $(OUTPUT_BASE)_*.mp4
