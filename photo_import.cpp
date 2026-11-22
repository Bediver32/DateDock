#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define NOMINMAX
#include <windows.h>
#include <shlobj.h>

#include <algorithm>
#include <chrono>
#include <cwchar>
#include <filesystem>
#include <fstream>
#include <future>
#include <map>
#include <regex>
#include <string>
#include <vector>

namespace fs = std::filesystem;
struct Day {
    std::wstring name;
    std::vector<fs::path> files;
};
struct Disk {
    std::wstring root;
    DWORD serial = 0;
};
HWND input, minimum, dates, statusLine, copyButton;
std::vector<Day> days;
std::wstring previous;
std::future<std::wstring> copying;

std::wstring text(HWND control) {
    wchar_t buffer[300]{};
    GetWindowTextW(control, buffer, 300);
    return buffer;
}

Disk findDisk(const std::wstring& name) {
    Disk match;
    const DWORD mask = GetLogicalDrives();
    for (int i = 0; i < 26; ++i) {
        if (!(mask & (1u << i))) continue;
        std::wstring root = std::wstring(1, wchar_t(L'A' + i)) + L":\\";
        const UINT type = GetDriveTypeW(root.c_str());
        if (type != DRIVE_FIXED && type != DRIVE_REMOVABLE) continue;
        wchar_t label[MAX_PATH]{};
        DWORD serial = 0;
        GetVolumeInformationW(root.c_str(), label, MAX_PATH, &serial, nullptr,
                              nullptr, nullptr, 0);
        if (_wcsicmp(root.substr(0, 2).c_str(), name.c_str()) == 0 ||
            _wcsicmp(root.c_str(), name.c_str()) == 0)
            return {root, serial};
        if (_wcsicmp(label, name.c_str()) == 0 && *label) {
            if (!match.root.empty()) throw std::runtime_error("duplicate label");
            match = {root, serial};
        }
    }
    return match;
}

void refresh(bool force = false) {
    try {
        const Disk disk = findDisk(text(input));
        const auto threshold = text(minimum);
        const auto key = disk.root + L"|" + std::to_wstring(disk.serial) + L"|" + threshold;
        if (!force && previous == key) return;
        previous = key;
        days.clear();
        SendMessageW(dates, LB_RESETCONTENT, 0, 0);
        if (disk.root.empty()) {
            SetWindowTextW(statusLine, L"Ожидание диска. Введите его букву или метку тома.");
            return;
        }
        if (!std::regex_match(threshold, std::wregex(L"[0-9]{1,9}"))) {
            SetWindowTextW(statusLine, L"Введите минимальный номер папки.");
            return;
        }

        int highest = std::stoi(threshold) - 1;
        fs::path latest;
        const std::wregex pattern(L"-?([0-9]{1,9})-?PHOTO", std::regex::icase);
        for (const auto& item : fs::directory_iterator(fs::path(disk.root) / L"DCIM")) {
            const auto name = item.path().filename().wstring();
            std::wsmatch match;
            if (!item.is_directory() || !std::regex_match(name, match, pattern)) continue;
            const int number = std::stoi(match[1].str());
            if (number > highest) {
                highest = number;
                latest = item.path();
            }
        }
        std::map<int, Day, std::greater<int>> groups;
        for (const auto& item : latest.empty() ? fs::directory_iterator{} :
                                               fs::directory_iterator(latest)) {
            if (!item.is_regular_file()) continue;
            WIN32_FILE_ATTRIBUTE_DATA info{};
            SYSTEMTIME utc{}, local{};
            if (!GetFileAttributesExW(item.path().c_str(), GetFileExInfoStandard, &info) ||
                !FileTimeToSystemTime(&info.ftLastWriteTime, &utc) ||
                !SystemTimeToTzSpecificLocalTime(nullptr, &utc, &local))
                throw std::runtime_error("file date");
            wchar_t formatted[16]{};
            std::swprintf(formatted, 16, L"%02d.%02d.%02d", local.wDay, local.wMonth,
                          local.wYear % 100);
            auto& day = groups[local.wYear * 10000 + local.wMonth * 100 + local.wDay];
            day.name = formatted;
            day.files.push_back(item.path());
        }
        for (auto& entry : groups) {
            auto& day = entry.second;
            const auto row = day.name + L"    ·    файлов: " + std::to_wstring(day.files.size());
            SendMessageW(dates, LB_ADDSTRING, 0, reinterpret_cast<LPARAM>(row.c_str()));
            days.push_back(std::move(day));
        }
        const auto message = latest.empty() ? L"Подходящих папок PHOTO в DCIM нет." :
            latest.wstring() + (days.empty() ? L" · файлов нет" : L" · выберите даты");
        SetWindowTextW(statusLine, message.c_str());
    } catch (const fs::filesystem_error&) {
        SetWindowTextW(statusLine, L"Не удалось прочитать DCIM или папку PHOTO.");
    } catch (const std::exception&) {
        days.clear();
        SendMessageW(dates, LB_RESETCONTENT, 0, 0);
        SetWindowTextW(statusLine, L"Проверьте метку диска и доступ к файлам.");
    }
}

bool identical(const fs::path& source, const fs::path& target) {
    if (!fs::is_regular_file(target) || fs::file_size(source) != fs::file_size(target))
        return false;
    std::ifstream a(source, std::ios::binary), b(target, std::ios::binary);
    if (!a || !b) return false;
    char x[65536], y[65536];
    do {
        a.read(x, sizeof x);
        b.read(y, sizeof y);
        if (a.gcount() != b.gcount() || !std::equal(x, x + a.gcount(), y)) return false;
    } while (a && b);
    return a.eof() && b.eof() && !a.bad() && !b.bad();
}

std::wstring importFiles(const std::vector<Day>& selected) {
    wchar_t desktop[MAX_PATH]{};
    if (FAILED(SHGetFolderPathW(nullptr, CSIDL_DESKTOPDIRECTORY, nullptr, 0, desktop)))
        return L"Не удалось найти рабочий стол.";
    const auto base = fs::path(desktop) / L"Фото с устройства";
    fs::create_directories(base);
    int done = 0, skipped = 0, errors = 0;
    std::wstring failed;
    for (const auto& day : selected) {
        const auto folder = base / day.name;
        for (const auto& source : day.files) {
            wchar_t temp[MAX_PATH]{};
            bool created = false;
            try {
                auto target = folder / source.filename();
                int suffix = 2;
                while (fs::exists(target) && !identical(source, target)) {
                    target = folder / (source.stem().wstring() + L" (" +
                        std::to_wstring(suffix++) + L")" + source.extension().wstring());
                }
                if (fs::exists(target)) {
                    ++skipped;
                    continue;
                }
                if (!GetTempFileNameW(base.c_str(), L"imp", 0, temp) ||
                    !CopyFileW(source.c_str(), temp, FALSE))
                    throw std::runtime_error("copy");
                created = fs::create_directory(folder);
                if (!MoveFileW(temp, target.c_str())) throw std::runtime_error("move");
                ++done;
            } catch (const std::exception&) {
                if (*temp) DeleteFileW(temp);
                if (created) RemoveDirectoryW(folder.c_str());
                failed = source.filename().wstring();
                ++errors;
            }
        }
    }
    return L"Скопировано: " + std::to_wstring(done) + L". Уже были: " +
           std::to_wstring(skipped) + L". Ошибок: " + std::to_wstring(errors) +
           (failed.empty() ? L"." : L". Последний файл: " + failed);
}

LRESULT CALLBACK windowProc(HWND window, UINT message, WPARAM w, LPARAM l) {
    switch (message) {
    case WM_CREATE: {
        auto control = [&](const wchar_t* kind, const wchar_t* title, DWORD style,
                           int x, int y, int width, int height, int id = 0) {
            HWND child = CreateWindowW(kind, title, WS_CHILD | WS_VISIBLE | style,
                x, y, width, height, window, reinterpret_cast<HMENU>(INT_PTR(id)),
                nullptr, nullptr);
            SendMessageW(child, WM_SETFONT,
                         reinterpret_cast<WPARAM>(GetStockObject(DEFAULT_GUI_FONT)), TRUE);
            return child;
        };
        control(L"STATIC", L"Буква или метка диска", 0, 16, 17, 172, 23);
        input = control(L"EDIT", L"E:", WS_BORDER | ES_AUTOHSCROLL, 190, 14, 230, 25);
        control(L"STATIC", L"Номер папки от", 0, 16, 52, 172, 23);
        minimum = control(L"EDIT", L"110", WS_BORDER | ES_NUMBER, 190, 49, 80, 25);
        control(L"BUTTON", L"Обновить", 0, 440, 14, 125, 27, 1);
        dates = control(L"LISTBOX", L"", WS_BORDER | WS_VSCROLL | LBS_EXTENDEDSEL,
                        16, 87, 549, 210);
        control(L"BUTTON", L"Все даты", 0, 16, 308, 120, 30, 2);
        copyButton = control(L"BUTTON", L"Копировать на рабочий стол", 0, 310, 308, 255, 30, 3);
        statusLine = control(L"STATIC", L"", 0, 16, 350, 549, 42);
        SetTimer(window, 1, 1250, nullptr);
        refresh();
        return 0;
    }
    case WM_TIMER:
        if (!copying.valid()) refresh();
        else if (copying.wait_for(std::chrono::seconds(0)) == std::future_status::ready) {
            try {
                SetWindowTextW(statusLine, copying.get().c_str());
            } catch (const std::exception&) {
                SetWindowTextW(statusLine, L"Не удалось записать файлы на рабочий стол.");
            }
            EnableWindow(copyButton, TRUE);
        }
        return 0;
    case WM_COMMAND:
        if (copying.valid()) return 0;
        if (LOWORD(w) == 1) refresh(true);
        if (LOWORD(w) == 2) SendMessageW(dates, LB_SETSEL, TRUE, -1);
        if (LOWORD(w) == 3) {
            refresh();
            std::vector<Day> selected;
            for (size_t i = 0; i < days.size(); ++i)
                if (SendMessageW(dates, LB_GETSEL, i, 0) > 0) selected.push_back(days[i]);
            if (selected.empty()) {
                SetWindowTextW(statusLine, L"Выберите одну или несколько дат.");
                return 0;
            }
            copying = std::async(std::launch::async, importFiles, std::move(selected));
            EnableWindow(copyButton, FALSE);
            SetWindowTextW(statusLine, L"Копирование…");
        }
        return 0;
    case WM_CLOSE:
        if (copying.valid()) return 0;
        DestroyWindow(window);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(window, message, w, l);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE, PWSTR, int show) {
    SetErrorMode(SEM_FAILCRITICALERRORS);
    WNDCLASSW wc{};
    wc.lpfnWndProc = windowProc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = GetSysColorBrush(COLOR_BTNFACE);
    wc.lpszClassName = L"PhotoImport";
    if (!RegisterClassW(&wc)) return 1;
    HWND window = CreateWindowW(wc.lpszClassName, L"Импорт файлов с устройства",
        WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
        CW_USEDEFAULT, CW_USEDEFAULT, 600, 450, nullptr, nullptr, instance, nullptr);
    if (!window) return 1;
    ShowWindow(window, show);
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return 0;
}
