cmake_minimum_required(VERSION 3.13)
project(pi_ball_detector LANGUAGES CXX)

# C++17
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Qt の MOC を自動化
set(CMAKE_AUTOMOC ON)
set(CMAKE_INCLUDE_CURRENT_DIR ON)


# 各種 find
find_package(OpenCV REQUIRED)
find_package(Boost REQUIRED)               # header-only ならこれで OK

find_package(PkgConfig REQUIRED)
pkg_check_modules(PISTACHE REQUIRED libpistache)

# nlohmann/json (ヘッダーオンリー)
find_path(NLOHMANN_JSON_INCLUDE_DIR 
  NAMES nlohmann/json.hpp
)
if(NOT NLOHMANN_JSON_INCLUDE_DIR)
  message(FATAL_ERROR "nlohmann/json.hpp が見つかりません。nlohmann-json3-dev をインストールしてください。")
endif()


  find_package(Qt5 COMPONENTS Widgets Network Multimedia REQUIRED)

# インクルードパス
include_directories(
  ${CMAKE_SOURCE_DIR}/src
  ${OpenCV_INCLUDE_DIRS}
  ${Boost_INCLUDE_DIRS}
  ${PISTACHE_INCLUDE_DIRS}
  ${NLOHMANN_JSON_INCLUDE_DIR}
)
link_directories(${PISTACHE_LIBRARY_DIRS})

  include_directories(
    ${Qt5Widgets_INCLUDE_DIRS}
    ${Qt5Network_INCLUDE_DIRS}
    ${Qt5Multimedia_INCLUDE_DIRS}
  )

# ソース／ヘッダ列挙
set(SOURCES
  src/main.cpp
  src/Capture.cpp
  src/Detector.cpp
  src/UdpSender.cpp
  src/ApiServer.cpp
)

  list(APPEND SOURCES src/Gui.cpp)

set(HEADERS
  src/Capture.hpp
  src/Detector.hpp
  src/UdpSender.hpp
  src/ApiServer.hpp
)

  list(APPEND HEADERS src/Gui.hpp)

# 実行ファイル生成
add_executable(pi_ball_detector
  ${SOURCES}
  ${HEADERS}
)

# リンク
target_link_libraries(pi_ball_detector
  ${OpenCV_LIBS}
  Boost::boost
  ${PISTACHE_LIBRARIES}
)

target_link_libraries(pi_ball_detector
Qt5::Widgets
Qt5::Network
Qt5::Multimedia
)

# インストール設定
install(TARGETS pi_ball_detector RUNTIME DESTINATION bin)
