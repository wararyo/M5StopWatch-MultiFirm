# Native tests (no ESP-IDF). The repository root CMakeLists.txt is the ESP-IDF component.
#   cmake -S tests -B build/tests
#   cmake --build build/tests
#   ctest --test-dir build/tests --output-on-failure
cmake_minimum_required(VERSION 3.16)
project(multifirm_native_tests CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

add_executable(test_layout test_layout.cpp)
target_include_directories(test_layout PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../src)
if(MSVC)
    target_compile_options(test_layout PRIVATE /W4 /WX)
else()
    target_compile_options(test_layout PRIVATE -Wall -Wextra -Werror)
endif()
add_test(NAME layout COMMAND test_layout ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/meta)

find_package(Python3 COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
    add_test(NAME python_tools
             COMMAND ${Python3_EXECUTABLE} -m unittest discover -s ${CMAKE_CURRENT_SOURCE_DIR} -p "test_*.py")
endif()
