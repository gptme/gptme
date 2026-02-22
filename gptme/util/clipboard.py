import platform
import subprocess

from . import get_installed_programs

text = ""


def set_copytext(new_text: str):
    global text
    text = new_text


def copy() -> bool:
    """return True if successful"""

    global text
    if platform.system() == "Linux":
        # check if xclip or wl-clipboard is installed
        installed = get_installed_programs(("xclip", "wl-copy"))
        if "wl-copy" in installed:
            output = subprocess.run(["wl-copy"], input=text, text=True, check=True)
            if output.returncode != 0:
                print("wl-copy failed to copy to clipboard.")
                return False
            return True
        if "xclip" in installed:
            output = subprocess.run(
                ["xclip", "-selection", "clipboard"], check=False, input=text, text=True
            )
            if output.returncode != 0:
                print("xclip failed to copy to clipboard.")
                return False
            return True
        print("No clipboard utility found. Please install xclip or wl-clipboard.")
        return False
    if platform.system() == "Darwin":
        output = subprocess.run(["pbcopy"], check=False, input=text, text=True)
        if output.returncode != 0:
            print("pbcopy failed to copy to clipboard.")
            return False
        return True

    return False
