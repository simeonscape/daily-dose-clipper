"""Merge this run's state.json into the one on GitHub, so two workflows
(scheduled Shorts and the live watcher) can't overwrite each other's posts.

    python statemerge.py MINE.json state.json   # writes the union into state.json
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))
import state as st  # noqa: E402

if __name__ == "__main__":
    mine_path, theirs_path = sys.argv[1], sys.argv[2]
    mine = st.load(mine_path)
    theirs = st.load(theirs_path)
    st.save(st.merge(mine, theirs), theirs_path)
    print(f"merged: {len(st.load(theirs_path)['posted'])} posted entries")
