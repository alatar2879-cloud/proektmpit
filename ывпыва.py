import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
import os
import re
import webbrowser
import networkx as nx

# === Автоматическая настройка пути к Graphviz ===
# Если Graphviz установлен стандартно — эти пути обычно подходят:
possible_paths = [
    r"C:\Program Files\Graphviz\bin",
    r"C:\Program Files (x86)\Graphviz\bin"
]

for path in possible_paths:
    if os.path.exists(path):
        os.environ["PATH"] += os.pathsep + path
        break

from graphviz import Source
import tempfile
import shutil
from PIL import Image, ImageTk

# ============================================================
#   ОЧИСТКА DOT-ТЕКСТА (замена длинных тире, скрытых символов)
# ============================================================

def sanitize_dot(text: str) -> str:
    replacements = {
        "—": "-",
        "–": "-",
        "−": "-",
        "‒": "-",
        "\u200b": "",
        "\ufeff": "",
        "​": "",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)

    # кавычим идентификаторы с дефисами
    patterns = [
        (re.compile(r'(?m)(^|[\s,(])([^\s,"\[\];()]+-[^\s,"\[\];()]+)(\s*\[)'), r'\1"\2"\3'),
        (re.compile(r'(?m)(^|[\s,(])([^\s,"\[\];()]+-[^\s,"\[\];()]+)(\s*;)'), r'\1"\2"\3'),
        (re.compile(r'(?m)(^|[\s,(])([^\s,"\[\];()]+-[^\s,"\[\];()]+)(\s*->)'), r'\1"\2"\3'),
        (re.compile(r'(?m)(^|[\s,(])([^\s,"\[\];()]+-[^\s,"\[\];()]+)(\s*--)'), r'\1"\2"\3'),
    ]

    for p, repl in patterns:
        text = p.sub(repl, text)

    text = re.sub(r'(?m)(^|[\s,(])([^\s,"\[\];()]+-[^\s,"\[\];()]+)$', r'\1"\2"', text)

    return text


# ============================================================
#   РЕНДЕР PNG ИЗ DOT
# ============================================================

def render_graph(dot_text, scale):
    tmpdir = tempfile.mkdtemp(prefix="dot_tmp_")
    outpath = os.path.join(tmpdir, "g")

    src = Source(dot_text)
    src.format = "png"
    src.render(outpath, cleanup=True)

    png = None
    for f in os.listdir(tmpdir):
        if f.lower().endswith(".png"):
            png = os.path.join(tmpdir, f)
            break

    if not png:
        shutil.rmtree(tmpdir)
        raise RuntimeError("Graphviz не создал PNG.")

    img = Image.open(png)
    w, h = img.size
    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    shutil.rmtree(tmpdir)
    return img


# ============================================================
#   ПАРСЕР УЗЛОВ DOT (поддержка label, work, URL/href)
# ============================================================

def extract_nodes_from_text(text: str):
    results = []

    pattern = re.compile(
        r'(?P<id>"[^"]+"|[A-Za-z0-9_\-]+)\s*\[\s*(?P<attrs>[^\]]*?)\s*\]',
        re.S
    )

    for m in pattern.finditer(text):
        raw_id = m.group("id")
        node_id = raw_id.strip('"')
        attrs = m.group("attrs")

        label = None
        work = None
        link = None

        lab = re.search(r'label\s*=\s*"([^"]*)"', attrs)
        if lab:
            label = lab.group(1)

        wk = re.search(r'work\s*=\s*"([^"]*)"', attrs, flags=re.I)
        if wk:
            work = wk.group(1)

        lk = re.search(r'(?:href|URL)\s*=\s*"([^"]*)"', attrs, flags=re.I)
        if lk:
            link = lk.group(1)

        results.append({
            "id": node_id,
            "label": label,
            "work": work,
            "link": link
        })

    return results


# ============================================================
#   ПАРСЕР SUBGRAPH / CLUSTER
# ============================================================

def parse_dot_groups(dot_text: str):
    groups = {}
    groups["Other"] = []

    lines = dot_text.splitlines()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # subgraph NAME {
        m = re.match(r'\s*subgraph\s+([^\s{]+)\s*\{', line)
        if m:
            group_id = m.group(1)
            block_lines = []
            brace_level = 0

            # вырезаем блок целиком
            while i < n:
                cur = lines[i]
                brace_level += cur.count("{")
                brace_level -= cur.count("}")
                block_lines.append(cur)
                i += 1
                if brace_level <= 0:
                    break

            block = "\n".join(block_lines)

            # название группы (если есть label)
            label = re.search(r'label\s*=\s*"([^"]+)"', block)
            group_name = label.group(1) if label else group_id

            nodes = extract_nodes_from_text(block)
            groups.setdefault(group_name, []).extend(nodes)
            continue

        else:
            # одиночное определение узла
            nodes = extract_nodes_from_text(line)
            if nodes:
                groups["Other"].extend(nodes)

        i += 1

    # глобальные узлы (для надёжности)
    global_nodes = extract_nodes_from_text(dot_text)
    existing = {nd["id"] for grp in groups.values() for nd in grp}

    for nd in global_nodes:
        if nd["id"] not in existing:
            groups["Other"].append(nd)
            existing.add(nd["id"])

    # нормализация work
    for grp in groups:
        for nd in groups[grp]:
            if not nd.get("label"):
                nd["label"] = nd["id"]

            if isinstance(nd.get("work"), str):
                nd["work"] = [w.strip() for w in nd["work"].split(";") if w.strip()]
            elif nd.get("work") is None:
                nd["work"] = []

    return groups


# ============================================================
#   ОСНОВНОЕ ПРИЛОЖЕНИЕ
# ============================================================

class GraphApp:
    def __init__(self, master):
        self.master = master
        master.title("Graph Viewer")
        master.geometry("1300x800")

        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.dot_text = ""

        # ---------- панель инструментов ----------
        toolbar = tk.Frame(master)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        tk.Button(toolbar, text="Открыть DOT", command=self.load_dot).pack(side=tk.LEFT, padx=4)
        tk.Button(toolbar, text="Люди и работы", command=self.open_people_window).pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="+", width=3, command=self.zoom_in).pack(side=tk.LEFT)
        tk.Button(toolbar, text="-", width=3, command=self.zoom_out).pack(side=tk.LEFT)
        tk.Button(toolbar, text="Центр", command=self.center_graph).pack(side=tk.LEFT, padx=4)

        # ---------- разметка ----------
        main = tk.PanedWindow(master, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        self.text_area = scrolledtext.ScrolledText(main, wrap=tk.NONE, font=("Consolas", 11))
        main.add(self.text_area, width=420)

        right = tk.Frame(main)
        main.add(right)

        self.canvas = tk.Canvas(right, bg="white")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # pan/zoom события
        self.canvas.bind("<ButtonPress-1>", self.pan_start)
        self.canvas.bind("<B1-Motion>", self.pan_move)
        self.canvas.bind("<MouseWheel>", self.zoom_mouse)

        # окно людей
        self.people_win = None
        self.people_tree = None
        self.people_detail = None
        self.current_groups = {}


    # ============================================================
    #   РАБОТА С ГРАФОМ
    # ============================================================

    def load_dot(self):
        path = filedialog.askopenfilename(filetypes=[("DOT files", "*.dot")])
        if not path:
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()

        self.dot_text = sanitize_dot(raw)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert(tk.END, self.dot_text)

        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.redraw()


    def redraw(self):
        if not self.dot_text.strip():
            return
        try:
            img = render_graph(self.dot_text, self.scale)
            self.img_obj = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(self.offset_x, self.offset_y, anchor="nw", image=self.img_obj)
        except Exception as e:
            messagebox.showerror("Ошибка рендера", str(e))


    def pan_start(self, event):
        self.pan_x = event.x
        self.pan_y = event.y

    def pan_move(self, event):
        dx = event.x - self.pan_x
        dy = event.y - self.pan_y
        self.offset_x += dx
        self.offset_y += dy
        self.pan_x = event.x
        self.pan_y = event.y
        self.redraw()

    def zoom_mouse(self, event):
        self.scale *= 1.1 if event.delta > 0 else 1/1.1
        self.redraw()

    def zoom_in(self):
        self.scale *= 1.1
        self.redraw()

    def zoom_out(self):
        self.scale /= 1.1
        self.redraw()

    def center_graph(self):
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0
        self.redraw()


    # ============================================================
    #   ОКНО "ЛЮДИ И ИХ РАБОТЫ"
    # ============================================================

    def open_people_window(self):
        if not self.dot_text.strip():
            messagebox.showinfo("Info", "Сначала загрузите DOT файл.")
            return

        if self.people_win and tk.Toplevel.winfo_exists(self.people_win):
            self.people_win.lift()
            return

        groups = parse_dot_groups(self.dot_text)
        self.current_groups = groups

        win = tk.Toplevel(self.master)
        win.title("Люди и их работы (группы)")
        win.geometry("800x600")
        self.people_win = win

        # панель поиска
        topf = tk.Frame(win)
        topf.pack(fill=tk.X, padx=6, pady=4)
        tk.Label(topf, text="Поиск:").pack(side=tk.LEFT)
        entry_var = tk.StringVar()
        entry = tk.Entry(topf, textvariable=entry_var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        # разделение
        split = tk.PanedWindow(win, orient=tk.HORIZONTAL)
        split.pack(fill=tk.BOTH, expand=True)

        # дерево
        tree_frame = tk.Frame(split)
        split.add(tree_frame, width=350)

        tree = ttk.Treeview(tree_frame, show="tree")
        tree.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.people_tree = tree

        scr = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        scr.pack(side=tk.RIGHT, fill=tk.Y)
        tree.configure(yscrollcommand=scr.set)

        # детальная панель
        detail = tk.Frame(split, relief=tk.RIDGE, borderwidth=1)
        split.add(detail, width=450)

        detail_title = tk.Label(detail, text="Выберите человека", font=("Arial", 12, "bold"))
        detail_title.pack(anchor="nw", padx=8, pady=8)

        detail_text = tk.Text(detail, wrap=tk.WORD, height=15)
        detail_text.pack(fill=tk.BOTH, expand=True, padx=8)
        detail_text.config(state=tk.DISABLED)

        link_btn = tk.Button(detail, text="Открыть ссылку", state=tk.DISABLED)
        link_btn.pack(pady=8)

        # -----------------------------
        #   заполнение дерева
        # -----------------------------
        def populate(filter_text=""):
            tree.delete(*tree.get_children())
            flt = filter_text.lower().strip()

            for grp_name, nodes in groups.items():
                parent = tree.insert("", "end", text=grp_name, open=True)

                for nd in nodes:
                    name = nd["label"]
                    wlist = nd["work"]

                    search_blob = (name + " " + " ".join(wlist)).lower()
                    if flt and flt not in search_blob:
                        continue

                    tree.insert(parent, "end", text=name, values=(nd["id"],))

        populate()

        # -----------------------------
        #   обработка выбора
        # -----------------------------
        def on_select(event):
            sel = tree.selection()
            if not sel:
                return

            iid = sel[0]
            parent = tree.parent(iid)

            # выбор группы
            if parent == "":
                detail_text.config(state=tk.NORMAL)
                detail_text.delete("1.0", tk.END)
                detail_text.insert(tk.END, f"Группа: {tree.item(iid, 'text')}")
                detail_text.config(state=tk.DISABLED)
                link_btn.config(state=tk.DISABLED)
                return

            # выбор человека
            name = tree.item(iid, "text")
            person = None

            for grp in groups.values():
                for nd in grp:
                    if nd["label"] == name or nd["id"] == name:
                        person = nd
                        break

            if not person:
                return

            detail_text.config(state=tk.NORMAL)
            detail_text.delete("1.0", tk.END)

            detail_text.insert(tk.END, f"Имя: {person['label']}\n")
            detail_text.insert(tk.END, f"ID: {person['id']}\n\n")

            if person["work"]:
                detail_text.insert(tk.END, "Работы:\n")
                for w in person["work"]:
                    detail_text.insert(tk.END, f" • {w}\n")
            else:
                detail_text.insert(tk.END, "Работы: нет данных\n")

            if person["link"]:
                detail_text.insert(tk.END, f"\nСсылка: {person['link']}")
                link_btn.config(
                    state=tk.NORMAL,
                    command=lambda url=person["link"]: webbrowser.open(url)
                )
            else:
                link_btn.config(state=tk.DISABLED)

            detail_text.config(state=tk.DISABLED)

        tree.bind("<<TreeviewSelect>>", on_select)

        # поиск
        def on_search(*args):
            populate(entry_var.get())

        entry_var.trace_add("write", on_search)

        win.transient(self.master)
        win.grab_set()
        win.focus()


# ============================================================
#   ЗАПУСК ПРИЛОЖЕНИЯ
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    app = GraphApp(root)
    root.mainloop()
