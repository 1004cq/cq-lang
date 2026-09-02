# CQ

CQ 是一门小语言：默认不可变、用 Result 处理错误、能跑解释器，也能编成 C 再交给 gcc。

```cq
print("hello CQ")

fn add(a: Int, b: Int) -> Int {
  a + b
}
```

## 运行

```bash
python3 cq.py run hello.cq
python3 cq.py check bad_types.cq
python3 cq.py build hello.cq
python3 cq.py native native.cq ./hello_native
python3 cq.py pkg init myapp
```

MIT License
