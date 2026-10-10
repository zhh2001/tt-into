// 输出本机 C++ 时钟类型性质，不修改任何时钟。
#include <chrono>
#include <iostream>
#include <type_traits>
int main() {
    std::cout << "{\"high_resolution_is_steady\":"
              << (std::chrono::high_resolution_clock::is_steady ? "true" : "false")
              << ",\"steady_is_steady\":"
              << (std::chrono::steady_clock::is_steady ? "true" : "false")
              << ",\"high_resolution_equals_system\":"
              << (std::is_same<std::chrono::high_resolution_clock,
                               std::chrono::system_clock>::value ? "true" : "false")
              << "}\n";
}
