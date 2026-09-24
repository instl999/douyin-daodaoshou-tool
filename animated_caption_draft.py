"""Generate a Jianying draft from Chinese copy with Volcengine Ark Agent Plan.

This file is the command - `python animated_caption_draft.py ...` - and stays
where the README says it is. The code lives in the daodaoshou package, one
module per part of the job:

    config      every setting, and the global speed
    storyboard  the director that splits the copy into scenes
    tts         narration          images   pictures
    assets      files named after their inputs, and what a run may reuse
    draft       the Jianying timeline: captions, title, camera, music, grade
    cli         the run from copy to draft

Every public name is re-exported here, so `import animated_caption_draft`
still reaches all of it.
"""

from daodaoshou.assets import *  # noqa: F403
from daodaoshou.bgm import *  # noqa: F403
from daodaoshou.camera import *  # noqa: F403
from daodaoshou.cli import *  # noqa: F403
from daodaoshou.cli import run
from daodaoshou.config import *  # noqa: F403
from daodaoshou.draft import *  # noqa: F403
from daodaoshou.env import *  # noqa: F403
from daodaoshou.images import *  # noqa: F403
from daodaoshou.jianying import *  # noqa: F403
from daodaoshou.layout import *  # noqa: F403
from daodaoshou.models import *  # noqa: F403
from daodaoshou.net import *  # noqa: F403
from daodaoshou.report import *  # noqa: F403
from daodaoshou.state import *  # noqa: F403
from daodaoshou.storyboard import *  # noqa: F403
from daodaoshou.styles import *  # noqa: F403
from daodaoshou.title import *  # noqa: F403
from daodaoshou.tts import *  # noqa: F403

if __name__ == "__main__":
    run()
