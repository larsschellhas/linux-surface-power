"""Block registry: algorithm GUID -> block class (spec blob-format 7).

Blocks are always looked up by GUID. The core's internal type numbers shift between core versions and are not used.
The GUIDs are interface identifiers needed to interoperate with the blob format.
"""

# Our names for the algorithm GUIDs (spec blob-format 7). Planned ones are listed so that the loader can name them in reports.
GUIDS = {
    "lookup_table": "3AFD73EE-A9A5-496E-A675-51EAFCB859B3",
    "min_max": "0AE20A40-02E5-42A1-961C-0CBFBB9EC380",
    "add_sub_scale": "FD5F6C7E-0BD1-4EA6-9075-82BADDEE2CB3",
    "multiplexer": "013CEB02-1BE7-4AC4-8362-252F6F47F5C0",
    "pass_through": "026CCFCC-A52A-4DB1-8B49-D2BAAFCAD329",
    "linear_map": "5974888D-F9F6-4132-AC67-D32E515C9B4E",
    "window_comparator": "2DB3B9EE-1148-477A-8217-B0CF0720BA37",
    "pid_sets": "9E2A7FED-9508-4006-9279-D3C6954F4B85",
    "pid_parameter_set": "83F47D50-2C46-49DB-A4A9-6D2AA48A1929",
}

# Interface GUID of internal nodes (VN<n>).
INTERNAL_NODE = "FB805E77-616E-4EA8-BAC1-6859A6C2D4B1"

# Filled by @register in the block modules.
BLOCKS: dict[str, type] = {}


def register(name: str):
    """Class decorator: register a block class under the GUID of `name`."""
    guid = GUIDS[name]

    def wrap(cls):
        cls.guid = guid
        cls.kind = name
        BLOCKS[guid] = cls
        return cls

    return wrap


def name_of(guid: str) -> str:
    for name, g in GUIDS.items():
        if g == guid:
            return name
    return "unknown"
