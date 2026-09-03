(function (global) {
  "use strict";

  function stringify(value) {
    if (value === null || value === undefined) return "None";
    if (value === true) return "true";
    if (value === false) return "false";
    if (Array.isArray(value)) return `[${value.map(stringify).join(", ")}]`;
    if (value && value.__cqResult) {
      return `${value.ok ? "Ok" : "Err"}(${stringify(value.value)})`;
    }
    return String(value);
  }

  function select(selector) {
    const node = document.querySelector(selector);
    if (!node) throw new Error(`CQ web: 找不到元素 ${selector}`);
    return node;
  }

  const web = {
    title(value) {
      document.title = stringify(value);
      return value;
    },
    html(selector, value) {
      select(selector).innerHTML = stringify(value);
      return value;
    },
    text(selector, value) {
      select(selector).textContent = stringify(value);
      return value;
    },
    on(selector, eventName, handler) {
      select(selector).addEventListener(eventName, handler);
      return handler;
    },
    value(selector, nextValue) {
      const node = select(selector);
      if (arguments.length > 1) node.value = stringify(nextValue);
      return node.value;
    },
    async fetchJson(url) {
      try {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return cqRt.ok(await response.json());
      } catch (error) {
        return cqRt.err(error instanceof Error ? error.message : String(error));
      }
    },
  };

  const cqRt = {
    modules: { web },
    env() {
      return {
        str: stringify,
        len: (value) => value.length,
        range: (count) => Array.from({ length: count }, (_, index) => index),
        map: (items, fn) => items.map(fn),
        filter: (items, fn) => items.filter(fn),
      };
    },
    print(value) {
      const text = stringify(value);
      console.log(text);
      const log = document.querySelector("#log");
      if (log) {
        log.textContent += `${text}\n`;
        log.scrollTop = log.scrollHeight;
      }
      return value;
    },
    interpolate(template, env) {
      return template.replace(/\{\s*([\p{L}_][\p{L}\p{N}_.]*)\s*\}/gu, (_, path) => {
        let value = env;
        for (const part of path.split(".")) value = value[part];
        return stringify(value);
      });
    },
    ok(value) {
      return { __cqResult: true, ok: true, value };
    },
    err(value) {
      return { __cqResult: true, ok: false, value };
    },
    struct(name, fields) {
      return Object.assign({ __cqType: name }, fields);
    },
    get(object, field) {
      if (object === null || object === undefined) {
        throw new Error(`CQ: 不能从 None 读取 ${field}`);
      }
      return object[field];
    },
    set(env, name, value) {
      let scope = env;
      while (scope && !Object.prototype.hasOwnProperty.call(scope, name)) {
        scope = Object.getPrototypeOf(scope);
      }
      (scope || env)[name] = value;
      return value;
    },
    call(fn, args) {
      if (typeof fn !== "function") throw new Error("CQ: 这个值不能调用");
      return fn(...args);
    },
    callMethod(object, name, args) {
      const fn = cqRt.get(object, name);
      if (typeof fn !== "function") throw new Error(`CQ: ${name} 不是函数`);
      return fn.apply(object, args);
    },
  };

  global.cqRt = cqRt;
})(globalThis);
