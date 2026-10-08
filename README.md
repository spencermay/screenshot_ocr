# screenshot_ocr

Local, offline screen-snip OCR for macOS, powered by a [GLM-OCR](https://ollama.com) model running under [Ollama](https://ollama.com). Select a region of your screen, and the recognized text is copied to your clipboard.

Produced (primarily) with Claude Sonnet 5.

## Requirements

- macOS (uses the native `screencapture` tool and notification banners)
- [Ollama](https://ollama.com) running locally:
    - ```bash
      curl -fsSL https://ollama.com/install.sh | sh
      ```
- Clone the repo:
    - ```bash
      cd Documents/Projects        # Or a folder of your choice
      git clone https://github.com/spencermay/screenshot_ocr
      ```
- Python 3 with `PyQt5` and `ollama`:
    - ```bash
      pip install PyQt5 ollama
      ```

## Setup

Pull the base model and build the anti-repeat variant used by default:

```bash
ollama pull glm-ocr
ollama create glm-ocr-no-repeat -f ./Modelfile
```

`Modelfile` sets low-temperature, repetition-penalized sampling to reduce the loop where the model re-emits the same text.

## Add a Mac Shortcut

Go to the Shortcuts app (e.g. press command+space and then type Shortcuts and press enter).

1. Press (+) - New Shortcut in the top right.

2. Search for the "Run shell script" action.

3. Drag the action into the main panel.

4. Replace `echo "Hello World"` with:
`python3 /PATH/TO/ocr_snip.py --task text`

You will have to replace `/PATH/TO/ocr_snip.py` with the path to your ocr_snip.py file (from your home directory). If you installed PyQt5 and ollama in a venv, you will have to replace python3 with the path to the python in the venv from the home directory.

5. Name the Shortcut in the top left-hand corner, if you would like

6. Click the info (i) button in the top right of the Shortcuts app and select the box next to "Run with: [    ]". Then hit the keys for your desired shorcut, like Command+Alt+3 or whatever you want.

7. You likely will have to grant permissions in Settings to the apps in which you want to use this Shortcut. If a dialogue comes up to grant the permission when you run it, that may work. Else you might have to grant permission in Settings/Privacy & Security/Screen & Audio Recording.

## Usage

### Snip and OCR (`ocr_snip.py`)

Run it, drag to select a screen region, and the text lands on your clipboard:

```bash
python3 ocr_snip.py                        # text recognition (default)
python3 ocr_snip.py --task table           # table recognition
python3 ocr_snip.py --task figure          # figure recognition
python3 ocr_snip.py --prompt "Formula Recognition:"   # custom prompt
python3 ocr_snip.py --model glm-ocr:latest # choose a different model
python3 ocr_snip.py --gui                  # full window UI instead of instant snip
```

Options:

| Flag | Description |
| --- | --- |
| `--task {text,table,figure}` | Built-in recognition preset (default: `text`) |
| `--prompt "..."` | Custom prompt, overrides `--task` |
| `--model NAME` | Ollama model/tag (default: `glm-ocr-no-repeat`) |
| `--gui` | Launch the window UI with a preview and task selector |

The tool streams the model's output and stops early if it detects a repetition loop, so a single snip returns quickly. Retina/HiDPI displays are handled correctly.

### Not a stable part of the project: LaTeXify numbered problems (`latexify_problems.py`)

Wraps a list of numbered problems in `\begin{problem}{N} ... \end{problem}` blocks. Reads stdin, writes stdout:

```bash
pbpaste | python3 latexify_problems.py | pbcopy   # transform clipboard contents
python3 latexify_problems.py < input.txt > out.tex
```

Any line beginning with `N.` starts a new problem; everything up to the next numbered line becomes its body. Trailing OCR cruft (hard-break spaces, extra blank lines, wrapping `"""`) is cleaned up.

## Files

- `ocr_snip.py` — screen-snip OCR tool (CLI + GUI)
- `latexify_problems.py` — wrap numbered problems in LaTeX `problem` environments
- `Modelfile` — Ollama definition for the `glm-ocr-no-repeat` model
