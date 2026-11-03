"""Use the packaged DOCX rasterizer with a Word-exported PDF on Windows."""

import importlib.util
import sys
from pathlib import Path

source, pdf, output = sys.argv[1:4]
helper=Path(r"C:\Users\张瑞涵\.codex\plugins\cache\openai-primary-runtime\documents\26.1026.10807\skills\documents\render_docx.py")
spec=importlib.util.spec_from_file_location("packaged_docx_renderer",helper)
renderer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
renderer.convert_to_pdf=lambda *args,**kwargs:(str(Path(pdf).resolve()),"PDF exported with installed Microsoft Word")
sys.argv=[str(helper),source,"--output_dir",output,"--emit_pdf"]
renderer.main()
