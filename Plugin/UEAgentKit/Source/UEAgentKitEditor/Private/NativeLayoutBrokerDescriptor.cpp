#include "NativeLayoutBrokerDescriptor.h"

#include "Dom/JsonObject.h"
#include "HAL/FileManager.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#include "Windows/AllowWindowsPlatformTypes.h"
#include <Aclapi.h>
#include <Sddl.h>
#include "Windows/HideWindowsPlatformTypes.h"
#pragma comment(lib, "Advapi32.lib")
#endif

namespace
{
	const TCHAR* DescriptorMissing = TEXT("native-preview-broker-descriptor-missing");
	const TCHAR* DescriptorInvalid = TEXT("native-preview-broker-descriptor-invalid");
	const TCHAR* ProtocolSchema = TEXT("ueak.layoutbroker/1.0");
	const TCHAR* ExpectedAlgorithmId = TEXT("p0-layered");
	const TCHAR* ExpectedAlgorithmVersion = TEXT("1.0.0");
	constexpr int32 MaxDescriptorBytes = 64 * 1024;

	FString Fail(FString& OutErrorCode, FString& OutErrorMessage, const TCHAR* Code, const TCHAR* Message)
	{
		OutErrorCode = Code;
		OutErrorMessage = Message;
		return FString();
	}

#if PLATFORM_WINDOWS
	bool GetCurrentUserSid(TArray<uint8>& OutSid, FString& OutReason)
	{
		HANDLE Token = nullptr;
		if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &Token) || Token == nullptr)
		{
			OutReason = TEXT("OpenProcessToken failed.");
			return false;
		}
		DWORD Size = 0;
		GetTokenInformation(Token, TokenUser, nullptr, 0, &Size);
		if (Size == 0)
		{
			CloseHandle(Token);
			OutReason = TEXT("TokenUser size failed.");
			return false;
		}
		TArray<uint8> Buffer;
		Buffer.SetNumUninitialized(static_cast<int32>(Size));
		if (!GetTokenInformation(Token, TokenUser, Buffer.GetData(), Size, &Size))
		{
			CloseHandle(Token);
			OutReason = TEXT("GetTokenInformation failed.");
			return false;
		}
		const TOKEN_USER* TokenUserInfo = reinterpret_cast<const TOKEN_USER*>(Buffer.GetData());
		if (TokenUserInfo == nullptr || TokenUserInfo->User.Sid == nullptr)
		{
			CloseHandle(Token);
			OutReason = TEXT("TokenUser SID is null.");
			return false;
		}
		const DWORD SidLength = GetLengthSid(TokenUserInfo->User.Sid);
		if (SidLength == 0)
		{
			CloseHandle(Token);
			OutReason = TEXT("GetLengthSid failed.");
			return false;
		}
		OutSid.SetNumUninitialized(static_cast<int32>(SidLength));
		if (!CopySid(SidLength, OutSid.GetData(), TokenUserInfo->User.Sid))
		{
			CloseHandle(Token);
			OutReason = TEXT("CopySid failed.");
			return false;
		}
		CloseHandle(Token);
		return true;
	}

	bool VerifyProtectedCurrentUserDacl(const FString& Path, FString& OutReason)
	{
		const DWORD Attributes = GetFileAttributesW(*Path);
		if (Attributes == INVALID_FILE_ATTRIBUTES)
		{
			OutReason = TEXT("GetFileAttributes failed.");
			return false;
		}
		if ((Attributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
		{
			OutReason = TEXT("Descriptor is a directory.");
			return false;
		}
		if ((Attributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
		{
			OutReason = TEXT("Descriptor is a reparse point.");
			return false;
		}

		TArray<uint8> CurrentSid;
		if (!GetCurrentUserSid(CurrentSid, OutReason))
		{
			return false;
		}

		PSID Owner = nullptr;
		PSID Group = nullptr;
		PACL Dacl = nullptr;
		PSECURITY_DESCRIPTOR SecurityDescriptor = nullptr;
		const DWORD Status = GetNamedSecurityInfoW(
			*Path,
			SE_FILE_OBJECT,
			DACL_SECURITY_INFORMATION,
			&Owner,
			&Group,
			&Dacl,
			nullptr,
			&SecurityDescriptor);
		if (Status != ERROR_SUCCESS)
		{
			OutReason = TEXT("GetNamedSecurityInfo failed.");
			return false;
		}
		auto Cleanup = [&SecurityDescriptor]()
		{
			if (SecurityDescriptor != nullptr)
			{
				LocalFree(SecurityDescriptor);
			}
		};
		if (Dacl == nullptr)
		{
			Cleanup();
			OutReason = TEXT("Descriptor has no DACL.");
			return false;
		}

		SECURITY_DESCRIPTOR_CONTROL Control = 0;
		DWORD Revision = 0;
		if (!GetSecurityDescriptorControl(SecurityDescriptor, &Control, &Revision))
		{
			Cleanup();
			OutReason = TEXT("Security descriptor control is unreadable.");
			return false;
		}
		if ((Control & SE_DACL_PRESENT) == 0)
		{
			Cleanup();
			OutReason = TEXT("DACL is absent.");
			return false;
		}
		if ((Control & SE_DACL_PROTECTED) == 0)
		{
			Cleanup();
			OutReason = TEXT("DACL is inherited, not protected.");
			return false;
		}

		ACL_SIZE_INFORMATION AclInfo;
		FMemory::Memzero(&AclInfo, sizeof(AclInfo));
		if (!GetAclInformation(Dacl, &AclInfo, sizeof(AclInfo), AclSizeInformation))
		{
			Cleanup();
			OutReason = TEXT("ACL information is unreadable.");
			return false;
		}
		if (AclInfo.AceCount != 1)
		{
			Cleanup();
			OutReason = TEXT("DACL must contain exactly one ACE.");
			return false;
		}
		LPVOID Ace = nullptr;
		if (!GetAce(Dacl, 0, &Ace) || Ace == nullptr)
		{
			Cleanup();
			OutReason = TEXT("DACL ACE is unreadable.");
			return false;
		}
		const ACE_HEADER* Header = reinterpret_cast<const ACE_HEADER*>(Ace);
		if (Header->AceType != ACCESS_ALLOWED_ACE_TYPE)
		{
			Cleanup();
			OutReason = TEXT("DACL ACE is not an allow ACE.");
			return false;
		}
		if ((Header->AceFlags & (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE | INHERIT_ONLY_ACE | NO_PROPAGATE_INHERIT_ACE)) != 0)
		{
			Cleanup();
			OutReason = TEXT("DACL ACE is inheritable.");
			return false;
		}
		const ACCESS_ALLOWED_ACE* AllowAce = reinterpret_cast<const ACCESS_ALLOWED_ACE*>(Ace);
		const PSID AceSid = reinterpret_cast<PSID>(const_cast<DWORD*>(&AllowAce->SidStart));
		if (!EqualSid(AceSid, CurrentSid.GetData()))
		{
			Cleanup();
			OutReason = TEXT("DACL ACE is not the current user.");
			return false;
		}
		Cleanup();
		return true;
	}
#else
	bool VerifyProtectedCurrentUserDacl(const FString& Path, FString& OutReason)
	{
		OutReason = TEXT("Current-user DACL proof is unavailable on this platform.");
		return false;
	}
#endif
}

FString FUEAgentKitLayoutBrokerDescriptor::DefaultPath()
{
	return FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("UEAgentKit"), TEXT("LayoutBroker.json"));
}

FString FUEAgentKitLayoutBrokerDescriptor::ComputeProjectPathHash()
{
	FString ProjectPath = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());
	FPaths::NormalizeFilename(ProjectPath);
	ProjectPath.ToLowerInline();
	FTCHARToUTF8 Utf8(*ProjectPath);
	uint8 Digest[20];
	FSHA1::HashBuffer(Utf8.Get(), static_cast<uint32>(Utf8.Length()), Digest);
	FString Hex;
	Hex.Reserve(40);
	for (const uint8 Byte : Digest)
	{
		Hex += FString::Printf(TEXT("%02x"), static_cast<uint32>(Byte));
	}
	return FString(TEXT("sha1:")) + Hex;
}

bool FUEAgentKitLayoutBrokerDescriptor::IsHexToken(const FString& Value)
{
	if (Value.Len() < 32 || Value.Len() > 128)
	{
		return false;
	}
	for (const TCHAR Character : Value)
	{
		if (!((Character >= TEXT('0') && Character <= TEXT('9'))
			|| (Character >= TEXT('a') && Character <= TEXT('f'))))
		{
			return false;
		}
	}
	return true;
}

bool FUEAgentKitLayoutBrokerDescriptor::ValidateFileShape(const FString& Path, FString& OutReason)
{
	const int64 Size = IFileManager::Get().FileSize(*Path);
	if (Size <= 0)
	{
		OutReason = TEXT("Descriptor is missing or empty.");
		return false;
	}
	if (Size > MaxDescriptorBytes)
	{
		OutReason = TEXT("Descriptor exceeds 64 KiB.");
		return false;
	}
	FString DaclReason;
	if (!VerifyProtectedCurrentUserDacl(Path, DaclReason))
	{
		OutReason = DaclReason;
		return false;
	}
	return true;
}

bool FUEAgentKitLayoutBrokerDescriptor::LoadFromPath(
	const FString& Path,
	FUEAgentKitLayoutBrokerDescriptor& OutDescriptor,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	if (!IFileManager::Get().FileExists(*Path))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorMissing, TEXT("The layout broker descriptor is missing."));
		return false;
	}
	FString ShapeReason;
	if (!ValidateFileShape(Path, ShapeReason))
	{
		Fail(
			OutErrorCode,
			OutErrorMessage,
			DescriptorInvalid,
			TEXT("The layout broker descriptor is not a current-user-only regular file."));
		return false;
	}

	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *Path))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The layout broker descriptor cannot be read."));
		return false;
	}
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The layout broker descriptor is not valid JSON."));
		return false;
	}
	if (Root->Values.Num() != 12)
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The layout broker descriptor field set is not exact."));
		return false;
	}

	FString SchemaVersion;
	FString Address;
	FString Token;
	FString ProjectName;
	FString ProjectPathHash;
	FString BrokerVersion;
	FString StartedAtText;
	FString AlgorithmIdValue;
	FString AlgorithmVersionValue;
	double Port = 0.0;
	double ProcessId = 0.0;
	double MaxConcurrentJobs = 0.0;
	const TArray<TSharedPtr<FJsonValue>>* Supported = nullptr;
	const TArray<TSharedPtr<FJsonValue>>* Algorithms = nullptr;
	if (!Root->TryGetStringField(TEXT("address"), Address)
		|| !Root->TryGetNumberField(TEXT("port"), Port)
		|| !Root->TryGetStringField(TEXT("token"), Token)
		|| !Root->TryGetStringField(TEXT("projectName"), ProjectName)
		|| !Root->TryGetStringField(TEXT("projectPathHash"), ProjectPathHash)
		|| !Root->TryGetStringField(TEXT("brokerVersion"), BrokerVersion)
		|| !Root->TryGetArrayField(TEXT("supportedContractVersions"), Supported)
		|| !Root->TryGetArrayField(TEXT("algorithms"), Algorithms)
		|| !Root->TryGetStringField(TEXT("startedAtUtc"), StartedAtText)
		|| !Root->TryGetNumberField(TEXT("processId"), ProcessId)
		|| !Root->TryGetNumberField(TEXT("maxConcurrentJobs"), MaxConcurrentJobs)
		|| !Root->TryGetStringField(TEXT("schemaVersion"), SchemaVersion))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The layout broker descriptor is missing required fields."));
		return false;
	}
	if (SchemaVersion != TEXT("ueak.layoutbroker-descriptor/1.0"))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor schemaVersion is unsupported."));
		return false;
	}
	if (Address != TEXT("127.0.0.1"))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor address is not 127.0.0.1."));
		return false;
	}
	if (!IsHexToken(Token))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor token is not 32-128 lowercase hex."));
		return false;
	}
	if (ProjectName != FApp::GetProjectName())
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor projectName does not match this Editor."));
		return false;
	}
	if (ProjectPathHash != ComputeProjectPathHash())
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor projectPathHash does not match this project."));
		return false;
	}
	const double PortRounded = FMath::RoundToDouble(Port);
	const double ProcessRounded = FMath::RoundToDouble(ProcessId);
	if (BrokerVersion.IsEmpty()
		|| Port < 1.0
		|| Port > 65535.0
		|| !FMath::IsNearlyEqual(Port, PortRounded)
		|| ProcessId <= 0.0
		|| !FMath::IsNearlyEqual(ProcessId, ProcessRounded)
		|| MaxConcurrentJobs != 1.0)
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor broker/port/process/concurrency fields are invalid."));
		return false;
	}

	bool bProtocolFound = false;
	for (const TSharedPtr<FJsonValue>& Entry : *Supported)
	{
		if (Entry.IsValid() && Entry->Type == EJson::String && Entry->AsString() == ProtocolSchema)
		{
			bProtocolFound = true;
			break;
		}
	}
	if (!bProtocolFound)
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor does not support ueak.layoutbroker/1.0."));
		return false;
	}

	bool bAlgorithmFound = false;
	for (const TSharedPtr<FJsonValue>& Entry : *Algorithms)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::Object)
		{
			continue;
		}
		const TSharedPtr<FJsonObject> Algorithm = Entry->AsObject();
		FString Id;
		FString Version;
		if (Algorithm.IsValid()
			&& Algorithm->TryGetStringField(TEXT("id"), Id)
			&& Algorithm->TryGetStringField(TEXT("version"), Version)
			&& Id == ExpectedAlgorithmId
			&& Version == ExpectedAlgorithmVersion)
		{
			AlgorithmIdValue = Id;
			AlgorithmVersionValue = Version;
			bAlgorithmFound = true;
			break;
		}
	}
	if (!bAlgorithmFound)
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor does not implement p0-layered/1.0.0."));
		return false;
	}

	FDateTime StartedAt;
	if (!FDateTime::ParseIso8601(*StartedAtText, StartedAt))
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor startedAtUtc is invalid."));
		return false;
	}
	const FTimespan Age = FDateTime::UtcNow() - StartedAt;
	if (Age.GetTotalSeconds() < -300.0 || Age.GetTotalHours() > 12.0)
	{
		Fail(OutErrorCode, OutErrorMessage, DescriptorInvalid, TEXT("The descriptor is stale or from the future."));
		return false;
	}

	OutDescriptor = {};
	OutDescriptor.Address = Address;
	OutDescriptor.Port = static_cast<int32>(Port);
	OutDescriptor.Token = Token;
	OutDescriptor.ProjectName = ProjectName;
	OutDescriptor.ProjectPathHash = ProjectPathHash;
	OutDescriptor.BrokerVersion = BrokerVersion;
	for (const TSharedPtr<FJsonValue>& Entry : *Supported)
	{
		if (Entry.IsValid() && Entry->Type == EJson::String)
		{
			OutDescriptor.SupportedContractVersions.Add(Entry->AsString());
		}
	}
	OutDescriptor.AlgorithmId = AlgorithmIdValue;
	OutDescriptor.AlgorithmVersion = AlgorithmVersionValue;
	OutDescriptor.StartedAtUtc = StartedAt;
	OutDescriptor.ProcessId = static_cast<int32>(ProcessId);
	OutDescriptor.MaxConcurrentJobs = static_cast<int32>(MaxConcurrentJobs);
	OutErrorCode.Reset();
	OutErrorMessage.Reset();
	return true;
}

bool FUEAgentKitLayoutBrokerDescriptor::Load(
	FUEAgentKitLayoutBrokerDescriptor& OutDescriptor,
	FString& OutErrorCode,
	FString& OutErrorMessage)
{
	return LoadFromPath(DefaultPath(), OutDescriptor, OutErrorCode, OutErrorMessage);
}
