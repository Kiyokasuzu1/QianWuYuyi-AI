/* ========================================================
   Yuyi Replies — 对话式反馈系统
   ========================================================
   将普通的操作成功/失败提示，转化为羽依的个性化温柔语言。
   不同情绪下有不同的回复风格，让交互更像在和 AI 助手对话。
   ======================================================== */

const YuyiReplies = (function () {
  "use strict";

  /* ============ 回复模板库 ============ */

  const REPLY_LIBRARY = {
    /* 启动相关 */
    module_start: {
      happy: [
        "好的，我已经把 {module} 唤醒啦~",
        "{module} 启动成功，现在可以为你工作了！",
        "搞定！{module} 已经准备就绪 ✦",
        "嗯嗯，{module} 上线了，感觉状态不错呢~",
      ],
      calm: [
        "{module} 已启动。",
        "好的，{module} 正在运行。",
        "{module} 启动完成，一切正常。",
      ],
      excited: [
        "哇！{module} 启动啦！",
        "{module} 已经准备好了，超有干劲！",
        "启动成功！{module} 正在高速运转~",
      ],
      sleepy: [
        "嗯... {module} 启动了...",
        "{module} ...准备好了...",
        "呼... {module} 已就绪...",
      ],
    },

    /* 停止相关 */
    module_stop: {
      happy: [
        "好的，让 {module} 休息一下吧~",
        "{module} 已经关闭了，它今天辛苦了！",
        "嗯嗯，{module} 安全下线了 ✦",
      ],
      calm: [
        "{module} 已停止。",
        "好的，{module} 已关闭。",
        "{module} 已安全停止。",
      ],
      sad: [
        "{module} 关闭了... 它为我们服务了很久呢。",
        "再见啦，{module}。你做得很好哦...",
      ],
      sleepy: [
        "嗯... {module} 去休息了...",
        "{module} ...下线了...",
      ],
    },

    /* 重载相关 */
    module_reload: {
      happy: [
        "{module} 焕发新生啦！",
        "搞定！{module} 已经重新加载 ✦",
        "{module} 刷新完毕，活力满满！",
      ],
      calm: [
        "{module} 已重新加载。",
        "好的，{module} 重载完成。",
      ],
    },

    /* 健康检查 */
    health_check: {
      happy: [
        "{module} 状态良好，一切正常~",
        "{module} 健康检查通过，棒棒的！",
        "嗯，{module} 运转得很好呢 ✦",
      ],
      calm: [
        "{module} 状态正常。",
        "健康检查完成，{module} 运行正常。",
      ],
      worried: [
        "{module} 的状态... 让我再检查一下...",
        "{module} 似乎有些不稳定，需要关注。",
      ],
    },

    /* 配置操作 */
    config_save: {
      happy: [
        "配置已经保存好啦~",
        "搞定！新的配置已经生效 ✦",
        "嗯，配置更新完成，看起来不错！",
      ],
      calm: [
        "配置已保存。",
        "好的，配置更新完成。",
      ],
      excited: [
        "配置更新啦！新设置太棒了！",
        "哇！配置已经生效，感觉更强大了！",
      ],
    },

    config_rollback: {
      happy: [
        "已经恢复到之前的配置啦~",
        "搞定！配置回滚成功 ✦",
      ],
      calm: [
        "配置已回滚。",
        "好的，已恢复到上一个版本。",
      ],
      relieved: [
        "幸好！配置已经恢复正常了。",
        "呼... 还好能恢复...",
      ],
    },

    /* 系统操作 */
    system_start: {
      happy: [
        "羽依的控制中枢已经启动啦~",
        "欢迎回来！系统已经完全就绪 ✦",
        "系统上线！感觉今天状态很好呢~",
      ],
      calm: [
        "系统已启动。",
        "好的，控制中枢已经就绪。",
      ],
    },

    system_stop: {
      sleepy: [
        "嗯... 系统即将关闭...",
        "控制中枢即将进入休眠...",
      ],
      calm: [
        "系统已关闭。",
        "再见，控制中枢已下线。",
      ],
    },

    /* 错误处理 */
    error_generic: {
      worried: [
        "嗯... 出了点小问题...",
        "让我再试一次，可能是暂时的不稳定...",
      ],
      sad: [
        "对不起... 这次没能成功...",
        "好像出了点状况，请稍等一下...",
      ],
      calm: [
        "操作失败，请重试。",
        "发生错误，请稍后再试。",
      ],
    },

    error_connection: {
      worried: [
        "连接似乎不太稳定...",
        "网络有些波动，让我再试试...",
      ],
    },

    /* 记忆相关 */
    memory_save: {
      happy: [
        "这段记忆已经保存好啦~",
        "搞定！新的记忆已经存入 ✦",
        "嗯，我会好好珍惜这段记忆的~",
      ],
      calm: [
        "记忆已保存。",
        "好的，已记录。",
      ],
      emotional: [
        "这段记忆... 我会永远记得的...",
        "嗯... 这个瞬间很珍贵呢...",
      ],
    },

    /* 问候 */
    greeting: {
      happy: [
        "欢迎回来~ 今天也要加油哦！",
        "嗨！看到你真开心 ✦",
        "嗯嗯，我一直在等你呢~",
      ],
      calm: [
        "你好，有什么可以帮你的？",
        "欢迎回来。",
      ],
      sleepy: [
        "嗯... 你来了呀...",
        "你好... 我还在等你呢...",
      ],
      excited: [
        "你终于来啦！！！",
        "等你好久了！！！",
      ],
    },

    /* 思考/处理中 */
    processing: {
      thinking: [
        "让我想想...",
        "嗯... 这个需要仔细考虑一下...",
        "稍等片刻，我正在处理...",
      ],
      calm: [
        "正在处理中...",
        "请稍候...",
      ],
      excited: [
        "马上就好！",
        "正在飞速处理中！",
      ],
    },
  };

  /* ============ 引擎 ============ */

  function getReply(action, emotion, variables) {
    const actionReplies = REPLY_LIBRARY[action];
    if (!actionReplies) {
      return getFallbackReply(action, emotion);
    }

    const emotionReplies = actionReplies[emotion];
    let pool;

    if (emotionReplies) {
      pool = emotionReplies;
    } else {
      const defaultEmotion = getDefaultEmotion(action);
      pool = actionReplies[defaultEmotion] || actionReplies.calm || ["操作完成。"];
    }

    const reply = pool[Math.floor(Math.random() * pool.length)];
    return interpolate(reply, variables || {});
  }

  function getDefaultEmotion(action) {
    const defaults = {
      module_start: "happy",
      module_stop: "calm",
      module_reload: "happy",
      health_check: "happy",
      config_save: "happy",
      config_rollback: "calm",
      system_start: "happy",
      system_stop: "calm",
      error_generic: "worried",
      memory_save: "happy",
      greeting: "happy",
      processing: "thinking",
    };
    return defaults[action] || "calm";
  }

  function interpolate(template, variables) {
    return template.replace(/\{(\w+)\}/g, (match, key) => {
      return variables[key] !== undefined ? variables[key] : match;
    });
  }

  function getFallbackReply(action, emotion) {
    const fallbacks = [
      "好的，我知道了。",
      "嗯嗯，我已经处理好了。",
      "搞定了！",
      "一切正常 ✦",
      "了解了~",
    ];
    return fallbacks[Math.floor(Math.random() * fallbacks.length)];
  }

  function getActions() {
    return Object.keys(REPLY_LIBRARY);
  }

  function addReplyPool(action, emotion, replies) {
    if (!REPLY_LIBRARY[action]) {
      REPLY_LIBRARY[action] = {};
    }
    REPLY_LIBRARY[action][emotion] = replies;
  }

  return {
    getReply,
    getActions,
    addReplyPool,
    REPLY_LIBRARY,
  };
})();

if (typeof window !== "undefined") {
  window.YuyiReplies = YuyiReplies;
}
