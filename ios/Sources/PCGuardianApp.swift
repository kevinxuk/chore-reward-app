//  PCGuardian iOS —— 儿童设备使用时间管控
//  与 Windows 版同源的状态机：锁定（打卡未完成）→ 恭喜 → 解锁倒计时 → 到时锁定
//  数据源：同一份 Gist chore-data.json（锁定 10 秒/解锁 60 秒双节奏轮询 + cache-buster）
//  整机管控依赖系统「引导式访问」把设备钉在本 App 上（见使用说明）。

import SwiftUI

@main
struct PCGuardianApp: App {
    @StateObject private var model = GuardianModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var model: GuardianModel

    var body: some View {
        switch model.mode {
        case .locked:    LockView()
        case .congrats:  CongratsView()
        case .unlocked:  UnlockedView()
        }
    }
}

// MARK: - 锁定画面

struct LockView: View {
    @EnvironmentObject var model: GuardianModel

    var body: some View {
        ZStack {
            Color(red: 0.06, green: 0.08, blue: 0.12).ignoresSafeArea()
            VStack(spacing: 18) {
                Spacer()
                Image(systemName: "lock.fill")
                    .font(.system(size: 72))
                    .foregroundColor(.white)
                if model.dailyUsedUp {
                    Text("⏰ 今天的电脑时间已经用完啦")
                        .font(.title2.bold())
                        .foregroundColor(Color(red: 1, green: 0.88, blue: 0.54))
                    Text("明天完成任务打卡后，可以获得新的时间哦\n（家长临时解锁除外）")
                        .font(.subheadline)
                        .foregroundColor(.gray)
                        .multilineTextAlignment(.center)
                } else if !model.everSynced {
                    Text("正在检查今日任务…")
                        .font(.title2.bold())
                        .foregroundColor(Color(red: 1, green: 0.88, blue: 0.54))
                } else if !model.netOk {
                    Text("☁️ 网络连接失败")
                        .font(.title2.bold())
                        .foregroundColor(Color(red: 1, green: 0.88, blue: 0.54))
                    Text("暂时无法读取打卡数据，保持锁定\n每 10 秒自动重试…")
                        .font(.subheadline)
                        .foregroundColor(.gray)
                        .multilineTextAlignment(.center)
                } else {
                    Text("📖 请先完成任务，再使用设备")
                        .font(.title2.bold())
                        .foregroundColor(Color(red: 1, green: 0.88, blue: 0.54))
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(model.required, id: \.id) { task in
                            HStack(spacing: 10) {
                                Image(systemName: model.doneMap[task.id] == true ? "checkmark.square" : "xmark.square")
                                    .foregroundColor(model.doneMap[task.id] == true ? .green : .white)
                                Text(task.name).foregroundColor(.white)
                            }
                            .font(.title3)
                        }
                    }
                    .padding(.top, 4)
                }
                Spacer()
                VStack(spacing: 6) {
                    Text(syncLine)
                        .font(.footnote).foregroundColor(.gray)
                    Text(model.clockText)
                        .font(.caption2).foregroundColor(.gray.opacity(0.6))
                }
            }
            .padding(24)
        }
    }

    private var syncLine: String {
        guard let t = model.lastSync else { return "" }
        let f = DateFormatter(); f.dateFormat = "HH:mm:ss"
        let verb = model.netOk ? "每 10 秒自动检查打卡" : "正在重试"
        return "☁️ 数据更新于 \(f.string(from: t)) · \(verb)"
    }
}

// MARK: - 恭喜画面

struct CongratsView: View {
    @EnvironmentObject var model: GuardianModel

    var body: some View {
        ZStack {
            Color(red: 0.07, green: 0.24, blue: 0.12).ignoresSafeArea()
            VStack(spacing: 14) {
                Spacer()
                Text("🎉 恭喜你！今日任务全部完成 🎉")
                    .font(.title.bold())
                    .foregroundColor(Color(red: 0.73, green: 0.96, blue: 0.78))
                Text("可以玩 \(model.minutes) 分钟，倒计时马上开始…")
                    .font(.title3)
                    .foregroundColor(.white)
                Spacer()
            }
        }
    }
}

// MARK: - 解锁画面（顶部居中倒计时 + 内嵌浏览器）

struct UnlockedView: View {
    @EnvironmentObject var model: GuardianModel

    var body: some View {
        VStack(spacing: 0) {
            // 顶部居中悬浮倒计时条
            HStack {
                Spacer()
                Text(hudText)
                    .font(.system(.headline, design: .monospaced))
                    .foregroundColor(hudColor)
                    .padding(.horizontal, 18).padding(.vertical, 8)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Color(red: 0.06, green: 0.08, blue: 0.12)))
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.gray.opacity(0.4)))
                Spacer()
            }
            .padding(.vertical, 8)
            .background(Color(.systemBackground))

            KidBrowser()
        }
        .ignoresSafeArea(.container, edges: .bottom)
    }

    private var hudText: String {
        if model.endTime == nil { return "🛠️ 家长临时解锁中" }
        let s = max(0, Int(model.remaining))
        return String(format: "⏰ 本次可用 %02d:%02d", s / 60, s % 60)
    }
    private var hudColor: Color {
        if model.endTime == nil { return Color(red: 1, green: 0.82, blue: 0.4) }
        return model.remaining > 60 ? Color(red: 0.49, green: 0.85, blue: 0.34) : .orange
    }
}

// MARK: - 内嵌浏览器（解锁期间使用）

import WebKit

struct KidBrowser: UIViewRepresentable {
    @EnvironmentObject var model: GuardianModel

    func makeUIView(context: Context) -> WKWebView {
        let w = WKWebView()
        w.allowsBackForwardNavigationGestures = true
        if let url = URL(string: GuardianModel.defaultHomePage) {
            w.load(URLRequest(url: url))
        }
        return w
    }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
