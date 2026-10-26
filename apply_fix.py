#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple Live 斗鱼卡顿 / 误判"未开播" 自动补丁
=================================================

用法（在仓库根目录，也就是包含 simple_live_app / simple_live_core 的那一层）：

    python apply_fix.py            # 默认当前目录
    python apply_fix.py D:\\code\\dart_simple_live

脚本做的事：对 3 个 Dart 文件做 12 处「精确文本替换」。
每一处都要求原文**恰好出现 1 次**，任何一处对不上就整体中止、不改任何文件
（避免出现改了一半、编不过的状态）。

改完后再执行：

    cd simple_live_app
    flutter pub get
    flutter build windows --release

如果报 "anchor 未找到"，说明你拉到的源码版本和本补丁不匹配，
按 README.md 里的手工步骤改那一段即可。
"""

import sys
import pathlib

# ---------------------------------------------------------------------------
# 待替换内容（旧文本 -> 新文本）
# ---------------------------------------------------------------------------

PC = "simple_live_app/lib/modules/live_room/player/player_controller.dart"
LC = "simple_live_app/lib/modules/live_room/live_room_controller.dart"
LP = "simple_live_app/lib/modules/live_room/live_room_page.dart"

PATCHES = []

# --- player_controller.dart : 让「缓冲大小」设置真正生效 ---------------------
PATCHES.append((
    "player_controller / bufferSize",
    PC,
    r'''  /// 播放器实例
  late final player = Player(
    configuration: PlayerConfiguration(
      title: "Simple Live Player",
      logLevel: AppSettingsController.instance.logEnable.value
          ? MPVLogLevel.info
          : MPVLogLevel.error,
    ),
  );''',
    r'''  /// 播放器实例
  late final player = Player(
    configuration: PlayerConfiguration(
      title: "Simple Live Player",
      bufferSize:
          AppSettingsController.instance.playerBufferSize.value * 1024 * 1024,
      logLevel: AppSettingsController.instance.logEnable.value
          ? MPVLogLevel.info
          : MPVLogLevel.error,
    ),
  );''',
))

# --- player_controller.dart : 桌面端 mpv 缓存/预读参数 ----------------------
PATCHES.append((
    "player_controller / initializePlayer 缓存参数",
    PC,
    r'''    // media_kit 仓库更新导致的问题，临时解决办法
    if(Platform.isAndroid){
      await pp.setProperty('force-seekable', 'yes');
    }
  }''',
    r'''    // media_kit 仓库更新导致的问题，临时解决办法
    if(Platform.isAndroid){
      await pp.setProperty('force-seekable', 'yes');
    }

    // 直播流抗抖动：加大 mpv 的网络缓存与预读（代价：起播慢 1~2 秒）
    if (!Platform.isAndroid && !Platform.isIOS) {
      try {
        await pp.setProperty('cache', 'yes');
        await pp.setProperty('cache-secs', '20');
        await pp.setProperty('demuxer-readahead-secs', '20');
        await pp.setProperty('network-timeout', '30');
      } catch (e) {
        Log.logPrint(e);
      }
    }
  }''',
))

# --- live_room_controller.dart : 新增字段 -----------------------------------
PATCHES.append((
    "live_room_controller / 新增字段",
    LC,
    r'''  // 开播时长状态变量
  var liveDuration = "00:00:00".obs;
  Timer? _liveDurationTimer;''',
    r'''  // 开播时长状态变量
  var liveDuration = "00:00:00".obs;
  Timer? _liveDurationTimer;

  // ---- 断流重连 / 缓冲监控 ----
  var reconnecting = false.obs;
  var reconnectFailed = false.obs;
  bool _reconnectingLock = false;
  int _sessionId = 0;
  int _rotateSeed = 0;
  DateTime? _lastReconnectAt;
  Timer? _bufferWatchTimer;
  StreamSubscription<bool>? _bufferingSubscription;
  StreamSubscription<bool>? _playingWatchSubscription;
  DateTime? _bufferingSince;
  bool _playbackStarted = false;
  bool _scrollScheduled = false;

  // ---- 屏蔽词正则缓存 ----
  List<Pattern> _shieldPatterns = [];
  String _shieldSignature = "";''',
))

# --- live_room_controller.dart : loadData 重置状态 --------------------------
PATCHES.append((
    "live_room_controller / loadData 重置",
    LC,
    r'''      SmartDialog.showLoading(msg: "");
      loadError.value = false;
      error = null;
      update();''',
    r'''      SmartDialog.showLoading(msg: "");
      loadError.value = false;
      error = null;
      reconnectFailed.value = false;
      _sessionId++;
      update();''',
))

# --- live_room_controller.dart : initPlaylist 变 Future<void> --------------
PATCHES.append((
    "live_room_controller / initPlaylist",
    LC,
    r'''  void initPlaylist() async {
    currentLineInfo.value = "线路${currentLineIndex + 1}";
    errorMsg.value = "";''',
    r'''  Future<void> initPlaylist() async {
    currentLineInfo.value = "线路${currentLineIndex + 1}";
    errorMsg.value = "";
    _bufferingSince = null;
    _playbackStarted = false;''',
))

# --- live_room_controller.dart : getPlayUrl 重置 ----------------------------
PATCHES.append((
    "live_room_controller / getPlayUrl 重置",
    LC,
    r'''    //重置错误次数
    mediaErrorRetryCount = 0;
    initPlaylist();
  }''',
    r'''    //重置错误次数
    mediaErrorRetryCount = 0;
    reconnectFailed.value = false;
    _bufferingSince = null;
    initPlaylist();
  }''',
))

# --- live_room_controller.dart : mediaEnd / mediaError 整体替换 -------------
PATCHES.append((
    "live_room_controller / mediaEnd + mediaError",
    LC,
    r'''  @override
  void mediaEnd() async {
    super.mediaEnd();
    if (mediaErrorRetryCount < 2) {
      Log.d("播放结束，尝试第${mediaErrorRetryCount + 1}次刷新");
      if (mediaErrorRetryCount == 1) {
        //延迟一秒再刷新
        await Future.delayed(const Duration(seconds: 1));
      }
      mediaErrorRetryCount += 1;
      //刷新一次
      setPlayer();
      return;
    }

    Log.d("播放结束");
    // 遍历线路，如果全部链接都断开就是直播结束了
    if (playUrls.length - 1 == currentLineIndex) {
      liveStatus.value = false;
    } else {
      changePlayLine(currentLineIndex + 1);

      //setPlayer();
    }
  }

  int mediaErrorRetryCount = 0;
  @override
  void mediaError(String error) async {
    super.mediaEnd();
    if (mediaErrorRetryCount < 2) {
      Log.d("播放失败，尝试第${mediaErrorRetryCount + 1}次刷新");
      if (mediaErrorRetryCount == 1) {
        //延迟一秒再刷新
        await Future.delayed(const Duration(seconds: 1));
      }
      mediaErrorRetryCount += 1;
      //刷新一次
      setPlayer();
      return;
    }

    if (playUrls.length - 1 == currentLineIndex) {
      errorMsg.value = "播放失败";
      SmartDialog.showToast("播放失败:$error");
    } else {
      //currentLineIndex += 1;
      //setPlayer();
      changePlayLine(currentLineIndex + 1);
    }
  }''',
    r'''  int mediaErrorRetryCount = 0;

  @override
  void mediaEnd() {
    super.mediaEnd();
    _tryReconnect("播放结束/断流");
  }

  @override
  void mediaError(String error) {
    super.mediaEnd();
    _tryReconnect("播放失败：$error");
  }

  void _tryReconnect(String reason) {
    final now = DateTime.now();
    if (_lastReconnectAt != null &&
        now.difference(_lastReconnectAt!).inSeconds < 3) {
      return;
    }
    _lastReconnectAt = now;
    Log.d("$reason，开始重连");
    reconnectPlayback();
  }

  /// 断流后重新取播放地址并重连。
  /// 关键：必须重新调用 getPlayUrls 拿新 token，而不是 jump 回旧地址。
  Future<void> reconnectPlayback() async {
    if (_reconnectingLock) {
      return;
    }
    final session = _sessionId;
    _reconnectingLock = true;
    reconnecting.value = true;
    try {
      for (var attempt = 0; attempt < 4; attempt++) {
        if (session != _sessionId || detail.value == null) {
          return;
        }
        if (attempt > 0) {
          await Future.delayed(Duration(seconds: 1 << attempt));
        }
        if (session != _sessionId || detail.value == null) {
          return;
        }
        try {
          final result = await site.liveSite.getPlayUrls(
            detail: detail.value!,
            quality: qualites[currentQuality],
          );
          if (result.urls.isEmpty) {
            continue;
          }
          var urls = result.urls;
          if (urls.length > 1) {
            // 每次重连换一条线路优先，避免一直撞同一个坏节点
            final shift = ++_rotateSeed % urls.length;
            urls = [...urls.skip(shift), ...urls.take(shift)];
          }
          playUrls.value = urls;
          playHeaders = result.headers;
          currentLineIndex = 0;
          mediaErrorRetryCount = 0;
          reconnectFailed.value = false;
          await initPlaylist();
          Log.d("重连成功（第 ${attempt + 1} 次尝试）");
          return;
        } catch (e) {
          Log.logPrint(e);
        }
      }
      if (session != _sessionId) {
        return;
      }
      // 只信平台接口，不再用“流结束”推断下播
      var living = false;
      try {
        living = await site.liveSite.getLiveStatus(roomId: roomId);
      } catch (e) {
        Log.logPrint(e);
      }
      liveStatus.value = living;
      if (living) {
        reconnectFailed.value = true;
        SmartDialog.showToast("重连失败，请点击刷新");
      }
    } finally {
      reconnecting.value = false;
      _reconnectingLock = false;
    }
  }

  /// 缓冲 watchdog：起播后连续缓冲超过 6 秒就换线路重连
  void initBufferWatch() {
    _bufferingSubscription?.cancel();
    _playingWatchSubscription?.cancel();
    _bufferWatchTimer?.cancel();
    _bufferingSince = null;
    _playbackStarted = false;

    _playingWatchSubscription = player.stream.playing.listen((playing) {
      if (playing) {
        _playbackStarted = true;
      }
    });
    _bufferingSubscription = player.stream.buffering.listen((buffering) {
      if (buffering) {
        _bufferingSince ??= DateTime.now();
      } else {
        _bufferingSince = null;
      }
    });
    _bufferWatchTimer = Timer.periodic(const Duration(seconds: 2), (_) {
      if (!liveStatus.value || !_playbackStarted || _bufferingSince == null) {
        return;
      }
      if (DateTime.now().difference(_bufferingSince!).inSeconds >= 6) {
        _bufferingSince = null;
        _playbackStarted = false;
        Log.d("缓冲超过 6 秒，换线路重连");
        reconnectPlayback();
      }
    });
  }''',
))

# --- live_room_controller.dart : onWSMessage 整体替换 -----------------------
PATCHES.append((
    "live_room_controller / onWSMessage",
    LC,
    r'''  /// 接收到WebSocket信息
  void onWSMessage(LiveMessage msg) {
    if (msg.type == LiveMessageType.chat) {
      if (messages.length > 200 && !disableAutoScroll.value) {
        messages.removeAt(0);
      }

      // 关键词屏蔽检查
      for (var keyword in AppSettingsController.instance.shieldList) {
        Pattern? pattern;
        if (Utils.isRegexFormat(keyword)) {
          String removedSlash = Utils.removeRegexFormat(keyword);
          try {
            pattern = RegExp(removedSlash);
          } catch (e) {
            // should avoid this during add keyword
            Log.d("关键词：$keyword 正则格式错误");
          }
        } else {
          pattern = keyword;
        }
        if (pattern != null && msg.message.contains(pattern)) {
          Log.d("关键词：$keyword\n已屏蔽消息内容：${msg.message}");
          return;
        }
      }

      messages.add(msg);

      WidgetsBinding.instance.addPostFrameCallback(
        (_) => chatScrollToBottom(),
      );
      if (!liveStatus.value || isBackground) {
        return;
      }

      addDanmaku([
        DanmakuContentItem(
          msg.message,
          color: Color.fromARGB(
            255,
            msg.color.r,
            msg.color.g,
            msg.color.b,
          ),
        ),
      ]);
    } else if (msg.type == LiveMessageType.online) {
      online.value = msg.data;
    } else if (msg.type == LiveMessageType.superChat) {
      superChats.add(msg.data);
    }
  }''',
    r'''  /// 接收到WebSocket信息
  void onWSMessage(LiveMessage msg) {
    if (msg.type == LiveMessageType.chat) {
      _refreshShieldPatterns();

      for (var pattern in _shieldPatterns) {
        if (msg.message.contains(pattern)) {
          return;
        }
      }

      // 批量裁剪，避免每条弹幕都 removeAt(0) 搬移整个列表
      if (!disableAutoScroll.value && messages.length > 250) {
        messages.removeRange(0, 50);
      }
      messages.add(msg);
      _scheduleScrollToBottom();

      if (!liveStatus.value || isBackground) {
        return;
      }

      addDanmaku([
        DanmakuContentItem(
          msg.message,
          color: Color.fromARGB(
            255,
            msg.color.r,
            msg.color.g,
            msg.color.b,
          ),
        ),
      ]);
    } else if (msg.type == LiveMessageType.online) {
      online.value = msg.data;
    } else if (msg.type == LiveMessageType.superChat) {
      superChats.add(msg.data);
    }
  }

  /// 屏蔽词只在列表变化时编译一次正则
  void _refreshShieldPatterns() {
    final list = AppSettingsController.instance.shieldList;
    final signature = list.join("\u0001");
    if (signature == _shieldSignature) {
      return;
    }
    _shieldSignature = signature;
    final patterns = <Pattern>[];
    for (var keyword in list) {
      if (keyword.isEmpty) {
        continue;
      }
      if (Utils.isRegexFormat(keyword)) {
        try {
          patterns.add(RegExp(Utils.removeRegexFormat(keyword)));
        } catch (e) {
          Log.d("关键词：$keyword 正则格式错误");
        }
      } else {
        patterns.add(keyword);
      }
    }
    _shieldPatterns = patterns;
  }

  /// 每帧最多滚动一次，而不是每条弹幕都排一个 postFrameCallback
  void _scheduleScrollToBottom() {
    if (_scrollScheduled) {
      return;
    }
    _scrollScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scrollScheduled = false;
      chatScrollToBottom();
    });
  }''',
))

# --- live_room_controller.dart : initDanmau 挂上 watchdog ------------------
PATCHES.append((
    "live_room_controller / initDanmau",
    LC,
    r'''  void initDanmau() {
    liveDanmaku.onMessage = onWSMessage;
    liveDanmaku.onClose = onWSClose;
    liveDanmaku.onReady = onWSReady;
  }''',
    r'''  void initDanmau() {
    liveDanmaku.onMessage = onWSMessage;
    liveDanmaku.onClose = onWSClose;
    liveDanmaku.onReady = onWSReady;
    initBufferWatch();
  }''',
))

# --- live_room_controller.dart : resetRoom 作废进行中的重连 ----------------
PATCHES.append((
    "live_room_controller / resetRoom",
    LC,
    r'''    rxSite.value = site;
    rxRoomId.value = roomId;''',
    r'''    rxSite.value = site;
    rxRoomId.value = roomId;
    _sessionId++;''',
))

# --- live_room_controller.dart : onClose 清理 ------------------------------
PATCHES.append((
    "live_room_controller / onClose",
    LC,
    r'''    liveDanmaku.stop();
    danmakuController = null;''',
    r'''    liveDanmaku.stop();
    _sessionId++;
    _bufferWatchTimer?.cancel();
    _bufferingSubscription?.cancel();
    _playingWatchSubscription?.cancel();
    danmakuController = null;''',
))

# --- live_room_page.dart : 浮层文案 ----------------------------------------
PATCHES.append((
    "live_room_page / 浮层文案",
    LP,
    r'''        Obx(
          () => Visibility(
            visible: !controller.liveStatus.value,
            child: const Center(
              child: Text(
                "未开播",
                style: TextStyle(fontSize: 16, color: Colors.white),
              ),
            ),
          ),
        ),''',
    r'''        Obx(
          () => Visibility(
            visible: !controller.liveStatus.value ||
                controller.reconnecting.value ||
                controller.reconnectFailed.value,
            child: Center(
              child: Text(
                controller.reconnecting.value
                    ? "正在重连…"
                    : controller.reconnectFailed.value
                        ? "重连失败，点击右上角 ⋮ 刷新"
                        : "未开播",
                style: const TextStyle(fontSize: 16, color: Colors.white),
              ),
            ),
          ),
        ),''',
))

# 补丁跑完后必须出现的关键标记
MARKERS = [
    (PC, "bufferSize:"),
    (LC, "reconnectPlayback"),
    (LC, "initBufferWatch"),
    (LC, "_refreshShieldPatterns"),
    (LP, "正在重连…"),
]


def main() -> int:
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    lc = root / LC
    if not lc.exists():
        print(f"[!] 找不到 {lc}")
        print("    请在仓库根目录运行（包含 simple_live_app 目录的那一层）")
        return 2

    # 先全部校验，再统一写入，避免改一半
    loaded = {}
    for name, rel, old, new in PATCHES:
        path = root / rel
        if path not in loaded:
            try:
                loaded[path] = path.read_text(encoding="utf-8")
            except OSError as exc:
                print(f"[!] 读取 {rel} 失败：{exc}")
                return 2
        count = loaded[path].count(old)
        if count != 1:
            print(f"[!] 锚点未匹配：{name}")
            print(f"    文件：{rel}")
            print(f"    期望出现 1 次，实际 {count} 次")
            print("    第一行是： " + old.splitlines()[0])
            print("    说明源码版本与补丁不一致，请按 README.md 手工修改这一处。")
            return 1

    for name, rel, old, new in PATCHES:
        path = root / rel
        loaded[path] = loaded[path].replace(old, new, 1)
        print(f"[+] {name}")

    for path, text in loaded.items():
        path.write_text(text, encoding="utf-8")
        print(f"[=] 已写入 {path.relative_to(root)}")

    for rel, marker in MARKERS:
        if marker not in (root / rel).read_text(encoding="utf-8"):
            print(f"[!] 校验失败：{rel} 里没有 {marker!r}")
            return 3

    print("\n完成。接下来：")
    print("  cd simple_live_app")
    print("  flutter pub get")
    print("  flutter build windows --release")
    print("产物在 simple_live_app/build/windows/x64/runner/Release/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
