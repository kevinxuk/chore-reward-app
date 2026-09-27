//  GuardianModel —— 与 Windows 版 guardian.pyw 同源的状态机
//  当天一次机会：会话记录 {date, end_time, unlock_start} 存 UserDefaults
//  （iOS 沙盒本身就是防篡改的，孩子无法 touching 文件）。

import Foundation
import SwiftUI

enum Mode { case locked, congrats, unlocked }

struct RequiredTask: Decodable, Identifiable {
    let id: String
    let name: String
}

@MainActor
final class GuardianModel: ObservableObject {
    static let defaultHomePage = "https://kevinxuk.github.io/chore-reward-app/yoyo.html"
    private static let gistBase = "https://gist.githubusercontent.com/kevinxuk/b96c91c9966987c90871a9dfb59520d9/raw/chore-data.json"
    private static let defaultRequiredIds = ["t13", "t17"]
    private static let defaultRequiredNames = ["阅读", "背单词"]
    private static let defaultMinutes = 30
    private static let congratsSeconds: TimeInterval = 6
    private static let pollLocked: TimeInterval = 10
    private static let pollUnlocked: TimeInterval = 60

    @Published var mode: Mode = .locked
    @Published var required: [RequiredTask] = []
    @Published var doneMap: [String: Bool] = [:]
    @Published var minutes: Int = defaultMinutes
    @Published var netOk = false
    @Published var everSynced = false
    @Published var lastSync: Date?
    @Published var remaining: TimeInterval = 0
    @Published var nowTick = Date()   // 每次心跳刷新，驱动时钟文本重绘

    var endTime: Date?
    private var unlockStart: Date?
    private var dailyUsedUpFlag = false
    private var congratsUntil: Date?
    private var pollTask: Task<Void, Never>?

    private let defaults = UserDefaults.standard
    private let sessionKeyDate = "sg_date"
    private let sessionKeyEnd = "sg_end"
    private let sessionKeyStart = "sg_start"

    var dailyUsedUp: Bool { dailyUsedUpFlag }
    var isParentUnlock: Bool { mode == .unlocked && endTime == nil }

    var clockText: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f.string(from: nowTick)
    }

    init() {
        loadSession()
        startTicker()
        pollTask = Task { await pollLoop() }
    }

    deinit { pollTask?.cancel() }

    // ---------- 轮询（锁定 10 秒 / 解锁 60 秒，双节奏 + cache-buster） ----------
    private func pollLoop() async {
        while !Task.isCancelled {
            let wait: TimeInterval = (mode == .unlocked) ? Self.pollUnlocked : Self.pollLocked
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            if Task.isCancelled { break }
            do {
                let urlStr = Self.gistBase + "?cb=\(Int(Date().timeIntervalSince1970))"
                let (data, _) = try await URLSession.shared.data(from: URL(string: urlStr)!)
                let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
                apply(state: obj)
                netOk = true
                everSynced = true
                lastSync = Date()
            } catch {
                netOk = false
            }
        }
    }

    // ---------- 数据应用（与 PC 版逻辑一致） ----------
    private func apply(state: [String: Any]) {
        let config = state["config"] as? [String: Any] ?? [:]
        let tasks = config["tasks"] as? [[String: Any]] ?? []
        let pc = config["computer"] as? [String: Any] ?? [:]

        // 必做任务：默认 t13/t17，对不上时按名称兜底
        var req: [RequiredTask] = []
        var used = Set<String>()
        let namesById = Dictionary(uniqueKeysWithValues: tasks.compactMap { t -> (String, String)? in
            guard let id = t["id"] as? String else { return nil }
            return (id, (t["name"] as? String) ?? "")
        })
        for tid in Self.defaultRequiredIds where namesById[tid] != nil {
            req.append(RequiredTask(id: tid, name: namesById[tid]!)); used.insert(tid)
        }
        if req.count < Self.defaultRequiredIds.count && !tasks.isEmpty {
            for hint in Self.defaultRequiredNames {
                if let t = tasks.first(where: { (($0["name"] as? String) ?? "").contains(hint) && !used.contains(($0["id"] as? String) ?? "") }),
                   let id = t["id"] as? String {
                    req.append(RequiredTask(id: id, name: (t["name"] as? String) ?? "")); used.insert(id)
                }
            }
        }
        if req.isEmpty {
            for (i, hint) in Self.defaultRequiredNames.enumerated() {
                req.append(RequiredTask(id: Self.defaultRequiredIds[i], name: hint + "（默认）"))
            }
        }

        let today = todayKey()
        let dayRec = (state["records"] as? [String: Any])? [today] as? [String: Any] ?? [:]
        var done: [String: Bool] = [:]
        for t in req { done[t.id] = ((dayRec[t.id] as? [String: Any])? ["done"] as? Bool) == true }

        if let m = pc["minutes"] as? Int, m > 0 { minutes = m } else { minutes = Self.defaultMinutes }
        let parentUnlock = (pc["unlock"] as? Bool) == true

        required = req
        doneMap = done

        let allDone = !req.isEmpty && req.allSatisfy { done[$0.id] == true }

        if parentUnlock {
            if mode != .unlocked || endTime != nil { enterUnlock(parent: true) }
            return
        }
        if dailyUsedUpFlag { return }   // 当天已用完：锁定到明天

        if allDone {
            switch mode {
            case .locked:
                if hasRestorableEnd, let end = savedEndForRestore {
                    savedEndForRestore = nil
                    hasRestorableEnd = false
                    restoreUnlock(end: end)     // 重启恢复剩余，不重置
                } else {
                    showCongrats()
                }
            case .congrats:
                break
            case .unlocked:
                if let start = unlockStart {
                    let newEnd = start.addingTimeInterval(TimeInterval(minutes * 60))
                    if let e = endTime, e != newEnd {
                        endTime = newEnd
                        saveSession()
                    }
                }
            }
        } else {
            if mode == .unlocked || mode == .congrats {
                // 打卡被取消：当天已发放过的不发新，直接锁定
                enterLocked(reason: "tasks")
            }
        }
    }

    // ---------- 模式切换 ----------
    private func showCongrats() {
        mode = .congrats
        congratsUntil = Date().addingTimeInterval(Self.congratsSeconds)
    }

    private func enterUnlock(parent: Bool, restoreEnd: Date? = nil) {
        mode = .unlocked
        unlockStart = Date()
        if parent {
            endTime = nil
        } else if let r = restoreEnd {
            endTime = r                      // 重启恢复剩余，不重置
        } else {
            endTime = unlockStart!.addingTimeInterval(TimeInterval(minutes * 60))
        }
        if !parent { saveSession() }
    }

    private func restoreUnlock(end: Date) {
        enterUnlock(parent: false, restoreEnd: end)
    }

    private func enterLocked(reason: String) {
        mode = .locked
        endTime = nil
        unlockStart = nil
        if reason == "tasks" { clearSession(keepToday: true) }
    }

    private func useUpToday() {
        dailyUsedUpFlag = true
        mode = .locked
        endTime = nil
        unlockStart = nil
    }

    // ---------- 心跳：恭喜超时/倒计时到点 ----------
    private func startTicker() {
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func tick() {
        nowTick = Date()
        switch mode {
        case .congrats:
            if let until = congratsUntil, Date() >= until {
                congratsUntil = nil
                enterUnlock(parent: false)
            }
        case .unlocked:
            if let end = endTime {
                remaining = end.timeIntervalSinceNow
                if remaining <= 0 {
                    // 到时：当天标记已用完并锁定（配合引导式访问即整机管控）
                    useUpToday()
                    saveSessionUsedUp()
                }
            }
        case .locked:
            break
        }
    }

    // ---------- 会话持久化（UserDefaults，沙盒防篡改） ----------
    private func saveSession() {
        guard let e = endTime, let s = unlockStart else { return }
        defaults.set(todayKey(), forKey: sessionKeyDate)
        defaults.set(e.timeIntervalSince1970, forKey: sessionKeyEnd)
        defaults.set(s.timeIntervalSince1970, forKey: sessionKeyStart)
    }

    private func saveSessionUsedUp() {
        // 保留过期 end（同 PC 版语义：当天已用完）
        defaults.set(todayKey(), forKey: sessionKeyDate)
    }

    private func clearSession(keepToday: Bool) {
        if keepToday, defaults.string(forKey: sessionKeyDate) == todayKey() {
            return   // 当天已发放过时长的记录不能被「取消打卡」洗掉
        }
        defaults.removeObject(forKey: sessionKeyDate)
        defaults.removeObject(forKey: sessionKeyEnd)
        defaults.removeObject(forKey: sessionKeyStart)
    }

    private func loadSession() {
        guard let date = defaults.string(forKey: sessionKeyDate) else { return }
        let end = defaults.double(forKey: sessionKeyEnd)
        let start = defaults.double(forKey: sessionKeyStart)
        let now = Date().timeIntervalSince1970
        guard date == todayKey(), start > 0, start <= now, end > 0 else {
            clearSession(keepToday: false)   // 非当天 → 清零
            return
        }
        if end > now {
            if end - now > 12 * 3600 || end - start > 24 * 3600 {
                clearSession(keepToday: false)
                return
            }
            savedEndForRestore = Date(timeIntervalSince1970: end)   // 首次打卡数据到达时恢复
            hasRestorableEnd = true
        } else {
            dailyUsedUpFlag = true   // 当天已用完
        }
    }

    private var savedEndForRestore: Date?
    private var hasRestorableEnd = false

    // ---------- 工具 ----------
    private func todayKey() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f.string(from: Date())
    }
}
