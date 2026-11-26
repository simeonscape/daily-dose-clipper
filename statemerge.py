"""Merge this run's state.json into the one on GitHub, so two workflows
(scheduled Shorts and the live watcher) can't overwrite each other's posts.

    python statemerge.py MINE.json state.json   # writes the union into state.json
    python statemerge.py --ai MINE.json ai_state.json   # same for Claude's usage/cache file
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))
import state as st  # noqa: E402

if __name__ == "__main__":
    if sys.argv[1] == "--ai":
        import ai  # noqa: E402
        mine_path, theirs_path = sys.argv[2], sys.argv[3]
        if not os.path.exists(mine_path):
            sys.exit(0)
        merged = ai.merge(ai.load(mine_path), ai.load(theirs_path))
        with open(theirs_path, "w") as f:
            json.dump(merged, f, indent=1, ensure_ascii=False)
        print(f"merged ai_state: {sum(merged['calls'].values())} calls in the last 2 weeks")
        sys.exit(0)
    mine_path, theirs_path = sys.argv[1], sys.argv[2]
    mine = st.load(mine_path)
    theirs = st.load(theirs_path)
    st.save(st.merge(mine, theirs), theirs_path)
    print(f"merged: {len(st.load(theirs_path)['posted'])} posted entries")
