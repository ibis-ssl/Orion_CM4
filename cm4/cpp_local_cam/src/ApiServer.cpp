#include "ApiServer.hpp"

#include <nlohmann/json.hpp>
#include <opencv2/imgcodecs.hpp>

using namespace Pistache;
using json = nlohmann::json;

ApiServer::ApiServer(Capture & capture, Detector & detector, int port) : capture_(capture), detector_(detector), port_(port) {}

ApiServer::~ApiServer() { stop(); }

void ApiServer::setupRoutes(Rest::Router & router)
{
  // GET /frame
  Rest::Routes::Get(router, "/frame", [&](const Rest::Request &, Http::ResponseWriter response) -> Rest::Route::Result {
    Frame frame;
    if (!capture_.getFrame(frame)) {
      response.send(Http::Code::Service_Unavailable, "No frame");
      return Rest::Route::Result::Failure;
    }
    std::vector<uchar> buf;
    cv::imencode(".jpg", frame.mat, buf, {cv::IMWRITE_JPEG_QUALITY, 80});
    response.headers().add<Pistache::Http::Header::ContentType>(MIME(Image, Jpeg));
    response.send(Http::Code::Ok, reinterpret_cast<const char *>(buf.data()), buf.size());
    return Rest::Route::Result::Ok;
  });

  // GET /params
  Rest::Routes::Get(router, "/params", [&](const Rest::Request &, Http::ResponseWriter response) -> Rest::Route::Result {
    auto [h1, s1, v1] = detector_.getHsvMin();
    auto [h2, s2, v2] = detector_.getHsvMax();
    json j;
    j["hsv_min"] = {h1, s1, v1};
    j["hsv_max"] = {h2, s2, v2};
    auto body = j.dump();
    response.send(Http::Code::Ok, body, MIME(Application, Json));
    return Rest::Route::Result::Ok;
  });

  // POST /params
  Rest::Routes::Post(router, "/params", [&](const Rest::Request & req, Http::ResponseWriter response) -> Rest::Route::Result {
    try {
      auto j = json::parse(req.body());
      auto mn = j.at("hsv_min");
      auto mx = j.at("hsv_max");
      detector_.setHsvMin(mn[0], mn[1], mn[2]);
      detector_.setHsvMax(mx[0], mx[1], mx[2]);
      response.send(Http::Code::Ok, "");
      return Rest::Route::Result::Ok;
    } catch (const std::exception & e) {
      response.send(Http::Code::Bad_Request, e.what());
      return Rest::Route::Result::Failure;
    }
  });
}

void ApiServer::start()
{
  Rest::Router router;
  setupRoutes(router);

  // オプションをビルド
  auto opts = Http::Endpoint::options().threads(2).flags(Tcp::Options::ReuseAddr);

  httpEndpoint_ = std::make_unique<Http::Endpoint>(Address(Ipv4::any(), Port(port_)));
  httpEndpoint_->init(opts);
  httpEndpoint_->setHandler(router.handler());

  serverThread_ = std::thread([&]() { httpEndpoint_->serve(); });
}

void ApiServer::stop()
{
  if (httpEndpoint_) {
    httpEndpoint_->shutdown();
  }
  if (serverThread_.joinable()) {
    serverThread_.join();
  }
}
