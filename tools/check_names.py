"""Ищет использование имён, которые нигде не определены на уровне модуля.
py_compile такое не ловит: это ошибка ВРЕМЕНИ ВЫПОЛНЕНИЯ, и в коде, где
исключения глотаются, она проявляется как «молча ничего не делает»."""
import ast, sys, builtins
src = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(src)
defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
for n in ast.walk(tree):
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defined.add(n.name)
    elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
        defined.add(n.id)
    elif isinstance(n, (ast.Import, ast.ImportFrom)):
        for a in n.names: defined.add((a.asname or a.name).split(".")[0])
    elif isinstance(n, ast.arg):
        defined.add(n.arg)
    elif isinstance(n, (ast.For, ast.comprehension)):
        t = n.target
        for x in ast.walk(t):
            if isinstance(x, ast.Name): defined.add(x.id)
    elif isinstance(n, ast.ExceptHandler) and n.name:
        defined.add(n.name)
    elif isinstance(n, (ast.With, ast.AsyncWith)):
        for it in n.items:
            if it.optional_vars:
                for x in ast.walk(it.optional_vars):
                    if isinstance(x, ast.Name): defined.add(x.id)
    elif isinstance(n, ast.Global):
        defined.update(n.names)
missing = sorted({n.id for n in ast.walk(tree)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                  and n.id not in defined})
if missing:
    print("НЕОПРЕДЕЛЁННЫЕ ИМЕНА:", ", ".join(missing)); sys.exit(1)
print("неопределённых имён нет")
