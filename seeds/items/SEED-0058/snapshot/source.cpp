/* mutated seed=0cc89d1a */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE36_Absolute_Path_Traversal__wchar_t_environment_ifstream_81_bad.cpp
Label Definition File: CWE36_Absolute_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 36 Absolute Path Traversal
 * BadSource: environment Read input from an environment variable
// qsw-canary-SEED-0058-c25416eb
 * GoodSource: Full path and file name
 * Sinks: ifstream
 *    BadSink : Open the file named in data using ifstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE36_Absolute_Path_Traversal__wchar_t_environment_ifstream_81.h"

#include <fstream>
using namespace std;

namespace CWE36_Absolute_Path_Traversal__wchar_t_environment_ifstream_81
{

void CWE36_Absolute_Path_Traversal__wchar_t_environment_ifstream_81_bad::vf46022(wchar_t * data) const
{

if (0) { /* dead */ } 
    {
        ifstream v971e0b;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        v971e0b.close();
        v971e0b.open((char *)data);
    }
}

}
#endif /* OMITBAD */
