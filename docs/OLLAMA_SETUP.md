# Ollama setup guide (free, local LLM)

This app never calls a paid API. All parsing is done by an open-source model
running **on your own machine** through [Ollama](https://ollama.com).
Your statements never leave your computer.

## 1. Install Ollama

| OS | Command / link |
|---|---|
| macOS | `brew install ollama` **or** download the app from <https://ollama.com/download> |
| Linux | `curl -fsSL https://ollama.com/install.sh \| sh` |
| Windows | Installer from <https://ollama.com/download> (WSL2 not required) |

Verify:

```bash
ollama --version
```

## 2. Start the server

The desktop app starts it for you (look for the llama icon in the menu bar /
tray). From a terminal:

```bash
ollama serve
```

It listens on `http://localhost:11434`. Test it:

```bash
curl http://localhost:11434/api/tags
```

## 3. Pull a model

Any instruction-tuned chat model works. Recommendations, all free:

| Model | Pull command | Size | Notes |
|---|---|---|---|
| **Llama 3.1 8B** (default) | `ollama pull llama3.1:8b` | ~4.9 GB | Great JSON compliance, good English |
| Qwen 2.5 7B | `ollama pull qwen2.5:7b` | ~4.7 GB | Best multilingual incl. Hebrew, strong at tables |
| Mistral 7B | `ollama pull mistral:7b` | ~4.1 GB | Fast, solid on English statements |
| Llama 3.2 3B | `ollama pull llama3.2:3b` | ~2 GB | For laptops with 8 GB RAM; use a smaller chunk size |

For Hebrew statements **Qwen 2.5** is usually the most accurate choice.

## 4. Hardware

* 8B models need ~6 GB of free RAM (they are 4-bit quantised).
* Apple Silicon and NVIDIA GPUs are used automatically; CPU-only works but a
  single statement page can take 30–90 s.
* Context: the app sends `num_ctx` (default 8192) with every request. Ollama's
  own default is only 2048, which truncates long pages — do not lower it.

## 5. Point the app at Ollama

Default is `http://localhost:11434`. To use another host/port set an env var
before starting Streamlit or change it in the sidebar:

```bash
OLLAMA_HOST=http://192.168.1.20:11434 streamlit run app.py
```

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Sidebar shows **Ollama offline** | Run `ollama serve`; check nothing else uses port 11434 |
| `Model 'x' not found` | `ollama pull x`, then refresh the page |
| Very slow / times out | Use a smaller model (`llama3.2:3b`) or reduce **Chunk size** in *Advanced* |
| Empty result / missing rows | Lower the chunk size to 3000–4000, try `qwen2.5:7b`, enable *Show extracted raw text* to check the PDF was readable |
| Hebrew comes out reversed | Set **Hebrew / RTL text fix** to `on` |
| Scanned PDF (image only) | Not supported in this MVP — run it through OCR first (e.g. `ocrmypdf`) |

## Offline UI testing without a model

`scripts/fake_ollama.py` is a 60-line stand-in that speaks the Ollama API and
returns canned rows for `samples/demo_statement.pdf`:

```bash
python scripts/fake_ollama.py 11435 &
OLLAMA_HOST=http://localhost:11435 streamlit run app.py
```
