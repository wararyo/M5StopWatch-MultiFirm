# Native tests (no ESP-IDF). The repository root CMakeLists.txt is the ESP-IDF component.
#   cmake -S tests -B build/tests
#   cmake --build build/tests
#   ctest --test-dir build/tests --output-on-failure
cmake_minimum_required(VERSION 3.16)
project(multifirm_native_tests C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

enable_testing()

add_executable(test_return_gesture test_return_gesture.cpp)
target_include_directories(test_return_gesture PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../src)
add_test(NAME return_gesture COMMAND test_return_gesture)

add_executable(test_layout test_layout.cpp)
target_include_directories(test_layout PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../src)
if(MSVC)
    target_compile_options(test_layout PRIVATE /W4 /WX)
else()
    target_compile_options(test_layout PRIVATE -Wall -Wextra -Werror)
endif()

# Optional SDK-adapter tests use real portable Mbed TLS SHA, with only IDF I/O
# mocked. Point at <esp-idf>/components/mbedtls/mbedtls (2.x or 3.x).
set(MULTIFIRM_MBEDTLS_DIR "" CACHE PATH "Mbed TLS source directory for guest tests")
if(MULTIFIRM_MBEDTLS_DIR)
    if(NOT Python3_Interpreter_FOUND)
        find_package(Python3 REQUIRED COMPONENTS Interpreter)
    endif()
    add_library(test_sha STATIC
        "${MULTIFIRM_MBEDTLS_DIR}/library/sha256.c"
        "${MULTIFIRM_MBEDTLS_DIR}/library/platform_util.c")
    target_include_directories(test_sha PUBLIC "${MULTIFIRM_MBEDTLS_DIR}/include" "${CMAKE_CURRENT_SOURCE_DIR}")
    target_compile_definitions(test_sha PUBLIC MBEDTLS_CONFIG_FILE="mbedtls_test_config.h")
    add_executable(test_guest test_guest.cpp guest_second.cpp)
    set_property(TARGET test_guest PROPERTY CXX_STANDARD 11)
    target_include_directories(test_guest PRIVATE stubs ../src)
    target_link_libraries(test_guest PRIVATE test_sha)
    add_executable(test_host test_host.cpp ../src/multifirm_host.cpp)
    set_property(TARGET test_host PROPERTY CXX_STANDARD 11)
    target_compile_definitions(test_host PRIVATE MULTIFIRM_HOST=1)
    target_include_directories(test_host PRIVATE stubs ../src)
    target_link_libraries(test_host PRIVATE test_sha)
    add_test(NAME guest_fixtures COMMAND ${Python3_EXECUTABLE}
        ${CMAKE_CURRENT_SOURCE_DIR}/guest_fixtures.py ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures)
    set_tests_properties(guest_fixtures PROPERTIES FIXTURES_SETUP guest_data)
    add_test(NAME guest COMMAND test_guest
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/table.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/app.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/legacy2.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/legacy3.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/standalone.bin)
    set_tests_properties(guest PROPERTIES FIXTURES_REQUIRED guest_data)
    add_test(NAME host COMMAND test_host
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/table.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/app.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta-bad-crc.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/default-app.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/invalid-project-app.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta-size-mismatch.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta-digest-mismatch.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta-elf-mismatch.bin
        ${CMAKE_CURRENT_BINARY_DIR}/guest-fixtures/meta-zero-elf.bin)
    set_tests_properties(host PROPERTIES FIXTURES_REQUIRED guest_data)
else()
    message(STATUS "Set MULTIFIRM_MBEDTLS_DIR to enable guest SDK-adapter tests")
endif()
add_test(NAME layout COMMAND test_layout ${CMAKE_CURRENT_SOURCE_DIR}/fixtures/meta)

# Keep assertions active even when tests are configured with Release.
foreach(test_target test_layout test_return_gesture test_guest test_host)
    if(TARGET ${test_target})
        if(MSVC)
            target_compile_options(${test_target} PRIVATE /W4 /WX /UNDEBUG)
        else()
            target_compile_options(${test_target} PRIVATE -Wall -Wextra -Werror -UNDEBUG)
        endif()
    endif()
endforeach()

find_package(Python3 COMPONENTS Interpreter)
if(Python3_Interpreter_FOUND)
    add_test(NAME python_tools
             COMMAND ${Python3_EXECUTABLE} -m unittest discover -s ${CMAKE_CURRENT_SOURCE_DIR} -p "test_*.py")
endif()
